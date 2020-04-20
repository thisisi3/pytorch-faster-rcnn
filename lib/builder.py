from .backbones import ResNet50, VGG16, ResLayerC5
from .region import MaxIoUAssigner, RandomSampler, SingleRoIExtractor, BasicRoIExtractor
from .necks import FPN
from .losses import FocalLoss, SmoothL1Loss, CrossEntropyLoss
from .detectors import RetinaNet, CascadeRCNN
from .heads import RetinaHead, RPNHead, RCNNHead
from torch.optim import SGD
from torchvision.ops import RoIAlign, RoIPool

import copy
from .utils import sum_list



_detectors_ = [RetinaNet, CascadeRCNN]
_backbones_ = [ResNet50, VGG16, ResLayerC5]
_necks_ = [FPN]
_heads_ = [RetinaHead, RPNHead, RCNNHead]
_losses_ = [CrossEntropyLoss, SmoothL1Loss, FocalLoss]
_roi_extractors_ = [SingleRoIExtractor, BasicRoIExtractor, RoIAlign, RoIPool]
_optimizers_ = [SGD]
_bbox_utils_ = [MaxIoUAssigner, RandomSampler]

_modules_ = sum_list([_detectors_, _backbones_, _necks_, _heads_, _losses_, _roi_extractors_, _optimizers_, _bbox_utils_])

MODULES = { cls.__name__:cls for cls in _modules_ }

print(MODULES)


def build_module(cfg, *args, **kwargs):
    cfg = copy.copy(cfg)
    assert 'type' in cfg
    m_type = cfg.pop('type')
    if m_type not in MODULES:
        raise ValueError("'{}' is not registered".format(m_type))
    return MODULES[m_type](*args, **cfg, **kwargs)

