import torchvision as tv
import torch
import torch.nn as nn
import torch.nn.functional as F
import logging
from . import config, utils
from torch.nn.modules.batchnorm import _BatchNorm


def decompose_vgg16():
    model = tv.models.vgg16(pretrained=True)
    features = list(model.features)[:30]
    classifier = model.classifier

    classifier = list(classifier)
    del classifier[6]
    del classifier[5]
    del classifier[2]
    classifier = nn.Sequential(*classifier)
        
    # freeze top4 conv
    for layer in features[:10]:
        for p in layer.parameters():
            p.requires_grad = False
            
    return nn.Sequential(*features), classifier

def make_vgg16_backbone(freeze_first_layers=True, transfer_backbone_cls=True):
    vgg16 = tv.models.vgg16(pretrained=True)
    feature_weights = vgg16.features[:30].state_dict()
    backbone = VGG16Backbone()
    backbone.features.load_state_dict(feature_weights)

    cls_weights = nn.Sequential(vgg16.classifier[0], vgg16.classifier[1],
                                vgg16.classifier[3], vgg16.classifier[4]).state_dict()
    classifier = VGG16Classifier()
    if transfer_backbone_cls:
        classifier.classifier.load_state_dict(cls_weights)
    else:
        utils.init_module_normal(classifier.classifier, mean=0.0, std=0.01)

    if freeze_first_layers:
        for layer in backbone.features[:10]:
            for p in layer.parameters():
                p.requires_grad = False

    return backbone, classifier

def make_res50_backbone_(freeze_first_layers=True,
                        transfer_backbone_cls=True,
                        fc_hidden_channels=1024):
    res50 = tv.models.resnet50(pretrained=True)
    # feature
    backbone = nn.Sequential(res50.conv1, res50.bn1, res50.relu, res50.maxpool,
                             res50.layer1, res50.layer2, res50.layer3)

    # classifier
    # 
    classifier = RCNNClassifier(in_channels=1024, fc_hidden_channels=fc_hidden_channels)
    utils.init_module_normal(classifier, mean=0.0, std=0.01)
    if freeze_first_layers:
        for i in range(4):
            backbone[i].requires_grad=False
    return backbone, classifier

def make_res50_backbone(freeze_first_layers=True,
                        transfer_backbone_cls=True,
                        fc_hidden_channels=1024):
    # TODO: so far make freeze and pretrained all true
    res50 = ResNet50()
    classifier = RCNNClassifier(in_channels=1024, fc_hidden_channels=fc_hidden_channels)
    utils.init_module_normal(classifier, mean=0.0, std=0.01)
    return res50, classifier
    
class VGG16(nn.Module):
    def __init__(self, freeze_first_layers=True, pretrained=True):
        super(VGG16, self).__init__()
        self.pretrained=pretrained
        self.freeze_first_layers=freeze_first_layers
        vgg16 = tv.models.vgg16(pretrained=pretrained)
        features = list(vgg16.features)[:30]
        self.features=nn.Sequential(*features)
        
        cls = list(vgg16.classifier)
        cls_ = nn.Sequential(cls[0], cls[1], cls[3], cls[4])
        self.classifier_ = [cls_]

        if freeze_first_layers:
            for layer in self.features[:10]:
                for p in layer.parameters():
                    p.requires_grad=False

    def init_weights(self):
        pass
    def get_classifier(self):
        return self.classifier[0]
    def forward(self, x):
        x = self.features(x)
        return x

    

class ResNet50(nn.Module):
    def __init__(self, frozen_stages=1, pretrained=True):
        super(ResNet50, self).__init__()
        self.frozen_stages = frozen_stages
        self.pretrained = pretrained
        res50 = tv.models.resnet50(pretrained=pretrained)
        self.conv1 = res50.conv1
        self.bn1 = res50.bn1
        self.relu = res50.relu
        self.maxpool = res50.maxpool
        self.layer1 = res50.layer1
        self.layer2 = res50.layer2
        self.layer3 = res50.layer3
        # self.layer4 = res50.layer4
        self.freeze_stages(frozen_stages)

    def init_weights(self):
        pass

    def train(self, mode=True):
        super(ResNet50, self).train(mode)
        self.freeze_stages(self.frozen_stages)
        if mode:
            for m in self.modules():
                if isinstance(m, _BatchNorm):
                    m.eval()

    def freeze_stages(self, stages):
        if stages >= 0:
            self.bn1.eval()
            for m in [self.conv1, self.bn1]:
                for param in m.parameters():
                    param.requires_grad=False
        for i in range(1, stages+1):
            m = getattr(self, 'layer{}'.format(i))
            m.eval()
            for param in m.parameters():
                param.requires_grad=False

    def forward(self, x):
        x = self.conv1(x)
        x = self.bn1(x)
        x = self.relu(x)
        x = self.maxpool(x)
        for i in range(1, 4):
            layer = getattr(self, 'layer{}'.format(i))
            x = layer(x)
        return x
    


class RCNNClassifier(nn.Module):
    def __init__(self,
                 in_channels,
                 fc_hidden_channels,
                 fc_out_channels=None,
                 device=None):
        super(RCNNClassifier, self).__init__()
        self.device=device
        if fc_out_channels is None:
            fc_out_channels=fc_hidden_channels
            self.fc_out_channels=fc_hidden_channels
        self.classifier = nn.Sequential(
            nn.Linear(in_channels*7*7, fc_hidden_channels, bias=True),
            nn.ReLU(inplace=True),
            nn.Linear(fc_hidden_channels, fc_out_channels, bias=True),
            nn.ReLU(inplace=True)
        )
        if device is not None:
            self.to(device)
        logging.info(('Constructed RCNNClassifier with in_channels={}, '
                      'fc_hidden_channels={}, fc_out_channels={}').format(
                          in_channels, fc_hidden_channels, fc_out_channels))

    def forward(self, x):
        return self.classifier(x)

class VGG16Classifier(nn.Module):
    def __init__(self, device=None):
        super(VGG16Classifier, self).__init__()
        self.device=device
        self.in_channels=512
        self.fc_out_channels=4096
        self.classifier = nn.Sequential(
            nn.Linear(25088, 4096, bias=True),
            nn.ReLU(inplace=True),
            nn.Linear(4096, 4096, bias=True),
            nn.ReLU(inplace=True)
        )
        if device is not None:
            self.to(device)

    def forward(self, x):
        return self.classifier(x)


class VGG16Backbone(nn.Module):
    def __init__(self, device=None):
        super(VGG16Backbone, self).__init__()
        self.device = device
        self.features = nn.Sequential(
            nn.Conv2d(3, 64, kernel_size=(3, 3), stride=1, padding=1),
            nn.ReLU(inplace=True),
            nn.Conv2d(64, 64, kernel_size=(3, 3), stride=1, padding=1),
            nn.ReLU(inplace=True),
            nn.MaxPool2d(kernel_size=2, stride=2, padding=0, dilation=1, ceil_mode=False),
            nn.Conv2d(64, 128, kernel_size=(3, 3), stride=1, padding=1),
            nn.ReLU(inplace=True),
            nn.Conv2d(128, 128, kernel_size=(3, 3), stride=1, padding=1),
            nn.ReLU(inplace=True),
            nn.MaxPool2d(kernel_size=2, stride=2, padding=0, dilation=1, ceil_mode=False),
            nn.Conv2d(128, 256, kernel_size=(3, 3), stride=(1, 1), padding=(1, 1)),
            nn.ReLU(inplace=True),
            nn.Conv2d(256, 256, kernel_size=(3, 3), stride=(1, 1), padding=(1, 1)),
            nn.ReLU(inplace=True),
            nn.Conv2d(256, 256, kernel_size=(3, 3), stride=(1, 1), padding=(1, 1)),
            nn.ReLU(inplace=True),
            nn.MaxPool2d(kernel_size=2, stride=2, padding=0, dilation=1, ceil_mode=False),
            nn.Conv2d(256, 512, kernel_size=(3, 3), stride=(1, 1), padding=(1, 1)),
            nn.ReLU(inplace=True),
            nn.Conv2d(512, 512, kernel_size=(3, 3), stride=(1, 1), padding=(1, 1)),
            nn.ReLU(inplace=True),
            nn.Conv2d(512, 512, kernel_size=(3, 3), stride=(1, 1), padding=(1, 1)),
            nn.ReLU(inplace=True),
            nn.MaxPool2d(kernel_size=2, stride=2, padding=0, dilation=1, ceil_mode=False),
            nn.Conv2d(512, 512, kernel_size=(3, 3), stride=(1, 1), padding=(1, 1)),
            nn.ReLU(inplace=True),
            nn.Conv2d(512, 512, kernel_size=(3, 3), stride=(1, 1), padding=(1, 1)),
            nn.ReLU(inplace=True),
            nn.Conv2d(512, 512, kernel_size=(3, 3), stride=(1, 1), padding=(1, 1)),
            nn.ReLU(inplace=True),
        )
        if device is not None:
            self.to(device)
            
    def forward(self, x):
        return self.features(x)

class RPN(nn.Module):
    r"""
    Region proposal network.
    """
    def __init__(self, num_classes, num_anchors, in_channels, hidden_channels):
        super(RPN, self).__init__()
        self.num_classes = num_classes
        self.num_anchors = num_anchors
        self.in_channels = in_channels
        self.hidden_channels = hidden_channels
        self.conv = nn.Conv2d(in_channels,
                              hidden_channels,
                              kernel_size=(3, 3), stride=(1, 1), padding=(1, 1))
        self.relu=nn.ReLU(inplace=True)
        self.classifier = nn.Conv2d(hidden_channels, num_anchors*2, kernel_size=(1, 1))
        self.regressor  = nn.Conv2d(hidden_channels, num_anchors*4, kernel_size=(1, 1))
        utils.init_module_normal(self.conv, mean=0.0, std=0.01)
        utils.init_module_normal(self.classifier, mean=0.0, std=0.01)
        utils.init_module_normal(self.regressor, mean=0.0, std=0.01)
        logging.info('Constructed RPN with in_channels={}, hidden_channels={}'.format(
            in_channels, hidden_channels))

    def forward(self, x):
        x = self.relu(self.conv(x))
        return self.classifier(x), self.regressor(x)

class RCNN(nn.Module):
    r"""
    The Fast RCNN or RCNN part of the detector, takes ROIs and classify them and adjust bboxes.
    Use weights of FC layers of a CNN backbone to initialize FC layers of the head, if possible.
    """
    def __init__(self, num_classes, cls_fc):
        super(RCNN, self).__init__()
        # in_features is 128 x 512 x 7 x 7 where 128 is batch size
        self.cls_fc = cls_fc
        self.classifier = nn.Linear(cls_fc.fc_out_channels, num_classes+1)
        self.regressor  = nn.Linear(cls_fc.fc_out_channels, (num_classes+1)*4)
        utils.init_module_normal(self.classifier, mean=0.0, std=0.01)
        utils.init_module_normal(self.regressor, mean=0.0, std=0.001)
        logging.info('Constructed RCNN with num_classes={}'.format(num_classes))

    # roi_batch is a batch of fixed tensors which is the result of ROIPooling
    def forward(self, roi_batch):
        if roi_batch is None or len(roi_batch) == 0:
            return None, None
        batch_size = roi_batch.shape[0]
        x = roi_batch.view(batch_size, -1)
        fc = self.cls_fc(x)
        cls_out = self.classifier(fc)
        reg_out = self.regressor(fc)
        return cls_out, reg_out


class ResLayerC5(nn.Module):
    def __init__(self, bn_requires_grad=True, pretrained=True):
        self.bn_requires_grad=bn_requires_grad
        super(ResLayerC5, self).__init__()
        res50 = tv.models.resnet50(pretrained=True)
        self.res_layer = res50.layer4
        
        if not bn_requires_grad:
            for m in self.modules():
                if isinstance(m, nn.BatchNorm2d):
                    m.requires_grad=False


    def train(self, mode=True):
        super(ResLayerC5, self).train(mode)
        for m in self.modules():
            if isinstance(m, nn.BatchNorm2d):
                m.eval()

    def forward(self, x):
        return self.res_layer(x)
    
    def init_weights(self):
        pass
