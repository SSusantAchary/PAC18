import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.utils.model_zoo as model_zoo
from collections import OrderedDict


class _DenseLayer(nn.Sequential):
    def __init__(self, num_input_features, growth_rate, bn_size, drop_rate):
        super(_DenseLayer, self).__init__()
        self.add_module('norm1', nn.BatchNorm3d(num_input_features)),
        self.add_module('relu1', nn.ReLU(inplace=True)),
        self.add_module('conv1', nn.Conv3d(num_input_features, bn_size *
                        growth_rate, kernel_size=1, stride=1, bias=False)),
        self.add_module('norm2', nn.BatchNorm3d(bn_size * growth_rate)),
        self.add_module('relu2', nn.ReLU(inplace=True)),
        self.add_module('conv2', nn.Conv3d(bn_size * growth_rate, growth_rate,
                        kernel_size=3, stride=1, padding = 1, bias=False)),
        self.drop_rate = drop_rate

    def forward(self, x):
        new_features = super(_DenseLayer, self).forward(x)
        if self.drop_rate > 0:
            new_features = F.dropout(new_features, p=self.drop_rate, training=self.training)
        return torch.cat([x, new_features], 1)


class _DenseBlock(nn.Sequential):
    def __init__(self, num_layers, num_input_features, bn_size, growth_rate, drop_rate):
        super(_DenseBlock, self).__init__()
        for i in range(num_layers):
            layer = _DenseLayer(num_input_features + i * growth_rate, growth_rate, bn_size, drop_rate)
            self.add_module('denselayer%d' % (i + 1), layer)


class _Strider(nn.Sequential):
    def __init__(self, num_input_features, num_output_features):
        super(_Strider, self).__init__()
        self.add_module('norm', nn.BatchNorm3d(num_input_features))
        self.add_module('relu', nn.ReLU(inplace=True))
        self.add_module('conv', nn.Conv3d(num_input_features, num_output_features,
                                        kernel_size=3, stride=1, bias=False)) ## reduce the size of the feature map
        self.add_module('pool', nn.Conv3d(num_output_features, num_output_features,
                                        kernel_size=2, stride=2))        ## removed the average pooling layer.


# class OutputTransition(nn.Module):   ##### original vnet implementation
#     def __init__(self, inChans, nll=True):
#         super(OutputTransition, self).__init__()
#         self.conv1 = nn.Conv3d(inChans, 2, kernel_size=5, padding=2)
#         self.bn1 = nn.BatchNorm3d(2)
#         self.conv2 = nn.Conv3d(2, 2, kernel_size=1)
#         self.relu1 = nn.ReLU(inplace=True)
#         if nll:
#             self.softmax = F.log_softmax
#         else:
#             self.softmax = F.softmax

#     def forward(self, x):
#         # convolve 32 down to 2 channels
#         out = self.relu1(self.bn1(self.conv1(x)))
#         out = self.conv2(out)

#         # make channels the last axis
#         out = out.permute(0, 2, 3, 4, 1).contiguous()
#         # flatten
#         out = out.view(out.numel() // 2, 2)
#         out = self.softmax(out)
#         # treat channel 0 as the predicted output
#         return out

class OutputTransition(nn.Module):
    def __init__(self, inChans, out_number):
        super(OutputTransition, self).__init__()
        self.bn1 = nn.BatchNorm3d(inChans)
        self.relu1 = nn.ReLU(inplace=True)
        self.conv1 = nn.Conv3d(inChans, out_number, kernel_size=5)
        self.conv3 = nn.Conv3d(4, out_number, kernel_size=2)


    def forward(self, x):
        out = self.conv1(self.relu1(self.bn1(x)))
        # out = self.conv3(self.relu1(out))
        # print (out)
        return out

class PhenotypeLayer(nn.Module):
    """docstring for PhenotypeLayer"""
    def __init__(self):
        super(PhenotypeLayer, self).__init__()
        self.layer1_c = nn.Linear(80, 32)
        self.layer1_a = nn.Linear(1, 32)
        self.layer1_t = nn.Linear(1, 32)
        self.layer2 = nn.Linear(32, 2)

    def forward(self, _class, _age, _tiv):
        out_c = self.layer1_c(_class)
        out_a = self.layer1_a(_age)
        out_t = self.layer1_t(_tiv)
        out = out_c + out_t + out_a
        out = self.layer2(out)
        return out

class DenseNet3D(nn.Module):
    """Densenet-BC model class, based on
    `"Densely Connected Convolutional Networks" <https://arxiv.org/pdf/1608.06993.pdf>`_

    Args:
        growth_rate (int) - how many filters to add each layer (`k` in paper)
        block_config (list of 4 ints) - how many layers in each pooling block
        num_init_features (int) - the number of filters to learn in the first convolution layer
        bn_size (int) - multiplicative factor for number of bottle neck layers
          (i.e. bn_size * k features in the bottleneck layer)
        drop_rate (float) - dropout rate after each dense layer
        out_number (int) - number of classification classe
    """
    def __init__(self, growth_rate=4, block_config=(1, 2, 3),
             num_init_features=8, bn_size=4, drop_rate=0.2, out_number=10):

        super(DenseNet3D, self).__init__()

        # First convolution
        self.features = nn.Sequential(OrderedDict([
            ('conv0', nn.Conv3d(1, num_init_features, kernel_size=3, stride=1, padding=2, bias=False)),
            ('norm0', nn.BatchNorm3d(num_init_features)),
            ('relu0', nn.ReLU(inplace=True)),
            ('pool0', nn.AvgPool3d(kernel_size=3, stride=2, padding=1)),# Average Pooling layer
        ]))

        # Each denseblock
        num_features = num_init_features
        for i, num_layers in enumerate(block_config):
            block = _DenseBlock(num_layers=num_layers, num_input_features=num_features,
                                bn_size=bn_size, growth_rate=growth_rate, drop_rate=drop_rate)
            self.features.add_module('db%d' % (i + 1), block)
            num_features = num_features + num_layers * growth_rate
            if i != len(block_config) - 1:
                trans = _Strider(num_input_features=num_features, num_output_features=num_features // 2)
                self.features.add_module('strider%d' % (i + 1), trans)
                num_features = num_features // 2

        # Final batch norm
        # self.features.add_module('norm5', nn.BatchNorm3d(num_features)) ### added OutputTransition

        # Linear layer
        # self.Linear_classifier = nn.Linear(8*8*8, num_classes)
        self.classifier   = OutputTransition(num_features, out_number)

        # Official init from torch repo.
        for m in self.modules():
            if isinstance(m, nn.Conv3d):
                nn.init.kaiming_normal(m.weight.data)
            elif isinstance(m, nn.BatchNorm3d):
                m.weight.data.fill_(1)
                m.bias.data.zero_()
            elif isinstance(m, nn.Linear):
                m.bias.data.zero_()

    def forward(self, x, age, tiv):
        # print ("DATA: ", x.size())
        features = self.features(x)
        # print ("features: ", features.size())
        out = F.relu(features, inplace=True)
        out = self.classifier(out)
        # print ("classifier: ", out.size())
        out = out.view(out.size(0), -1)
        # print ("linear: ", out.size())
        out = PhenotypeLayer().cuda()(out, age, tiv)
        # print ("phType: ", out.size())
        out = F.softmax(out)
        return out
