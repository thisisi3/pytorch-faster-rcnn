import torch.nn as nn
from mmdet.ops.dcn import DeformConv
from mmcv.cnn import normal_init
import logging
from .. import debug

# shape_outs [[1, 2, 267, 200], [1, 2, 134, 100], ...]
# anchors [[4, num_anchors, 267, 200], [4, num_anchors, 134, 100], ...]
# img_metas, mainly need img_shape
#
def shape_target_single_image(shape_outs, anchors, img_meta, assigner, sampler, gt_bbox, gt_label, assigner, sampler,
                              target_means=None, target_stds=None):
    from ..builder import build_module
    img_size = img_meta['img_shape'][-2:]
    grid_sizes = [so.shape[-2:] for so in shape_outs]
    num_anchors = anchors.shape[1]
    
    pass

# it is actually multi-level guided anchor
class GuidedAnchor(nn.Module):
    def __init__(self,
                 in_channels=256,
                 out_channels=256,
                 anchor_scales=None,
                 anchor_ratios=None,
                 anchor_strides=None, 
                 anchoring_means=[0.0, 0.0, 0.0, 0.0],
                 anchoring_stds=[0.07, 0.07, 0.14, 0.14],
                 deformable_groups=4,
                 sigma=8,
                 loc_filter_thr=0.01,
                 loss_loc=None,
                 loss_shape=None):
        super(GuidedAnchor, self).__init__()
        self.in_channels=in_channels
        self.out_channels=out_channels
        self.anchor_scales=anchor_scales
        self.anchor_ratios=anchor_ratios
        self.anchor_strides=anchor_strides
        self.anchoring_means=anchoring_means
        self.anchoring_stds=anchoring_stds
        self.deformable_groups=deformable_groups
        self.sigma=sigma
        self.loc_filter_thr=loc_filter_thr
        
        from ..builder import build_module
        self.loss_loc=build_module(loss_loc)
        self.loss_shape=build_module(loss_shape)

        self.init_layers()
        

    def init_layers(self):
        self.loc_layer = nn.Conv2d(self.in_channels, 1, 3, padding=1)
        self.shape_layer = nn.Conv2d(self.in_channels, 2, 3, padding=1)

        offset_channels = 3 * 3 * 2 * self.deformable_groups
        self.offset_layer = nn.Conv2d(2, offset_channels, 1, bias=False)
        # Do we turn bias on?
        self.adapt_layer = DeformConv(self.in_channels, self.out_channels, 3, padding=1,
                                      deformable_groups=self.deformable_groups)
        self.relu=nn.ReLU(inplace=True)



    def init_weights(self):
        normal_init(self.offset_layer, std=0.1)
        normal_init(self.adapt_layer, std=0.01)

    def create_anchor(self):
        pass

    def shape_target_single_image(self, )


    def forward(self, feats):
        assert len(feats) == len(self.anchor_strides)
        loc_outs = [self.loc_layer(feat).sigmoid() for feat in feats]
        shape_outs = [self.shape_layer(feat) for feat in feats]
        offsets = [self.offset_layer(so.detach()) for so in shape_outs]
        adapt_feats = [self.relu(self.adapt_layer(feats[i], offsets[i])) for i in range(len(feats))]
        shape_reformed_outs = [self.sigma*self.anchor_strides[i]*(shape_outs[i].exp()) for i in range(len(feats))]
        return loc_outs, shape_outs, shape_reformed_outs, adapt_feats

    def loc_target(self):
        pass

    def loss(self, loc_outs, shape_outs, shape_reformed_outs, adapt_feats, gt_bboxes, gt_labels, img_metas):
        pass


class GARPNHead(nn.Module):

    def __init__(self,
                 in_channels=256,
                 feat_channels=256,
                 octave_base_scale=8,
                 scales_per_octave=3,
                 octave_ratios=[0.5, 1.0, 2.0],
                 anchor_strides=[4, 8, 16, 32, 64],
                 anchoring_means=[0.0, 0.0, 0.0, 0.0],
                 anchoring_stds=[0.07, 0.07, 0.14, 0.14],
                 target_means=[0.0, 0.0, 0.0, 0.0],
                 target_stds=[0.07, 0.07, 0.11, 0.11],
                 deformable_groups=4,
                 loc_filter_thr=0.01,
                 loss_loc=dict(
                     type='FocalLoss',
                     use_sigmoid=True,
                     gamma=2.0,
                     alpha=0.25,
                     loss_weight=1.0),
                 loss_shape=dict(type='BoundedIoULoss', beta=0.2, loss_weight=1.0),
                 loss_cls=dict(type='CrossEntropyLoss', use_sigmoid=True, loss_weight=1.0),
                 loss_bbox=dict(type='SmoothL1Loss', beta=1.0, loss_weight=1.0)):
        super(GARPNHead, self).__init__()
        self.in_channels=in_channels
        self.feat_channels=feat_channels
        self.octave_base_scale=octave_base_scale
        self.scales_per_octave=scales_per_octave
        self.octave_ratios=octave_ratios

        self.anchor_strides=anchor_strides
        self.anchoring_means=anchoring_means
        self.anchoring_stds=anchoring_stds
        self.target_means=target_means
        self.target_stds=target_stds
        self.loc_filter_thr=loc_filter_thr
        
        self.loss_cls_cfg = loss_cls
        self.loss_bbox_cfg = loss_bbox
        self.loss_loc_cfg = loss_loc
        self.loss_shape_cfg = loss_shape

        from ..builder import build_module
        self.loss_cls = build_module(loss_cls)
        self.loss_bbox = build_module(loss_bbox)

        octave_scales = [2**(i/scales_per_octave) for i in range(scales_per_octave)]
        anchor_scales = [octave_base_scale*octave_scale for octave_scale in octave_scales]

        self.guided_anchor = GuidedAnchor(
            in_channels,
            in_channels,
            anchor_scales=octave_scales,
            anchor_ratios=octave_ratios,
            anchor_strides=anchor_strides,
            anchoring_means=anchoring_means,
            anchoring_stds=anchoring_stds,
            deformable_groups=deformable_groups,
            loc_filter_thr=loc_filter_thr,
            loss_loc=loss_loc,
            loss_shape=loss_shape)
        
        self.num_anchors = 1
        self.num_classes = 2
        self.cls_channels = 1
        self.use_sigmoid = loss_cls.get('use_sigmoid', False)

        self.init_layers()

    def init_layers(self):
        self.conv = nn.Conv2d(self.in_channels, self.feat_channels, 3, padding=1)
        self.relu = nn.ReLU(inplace=True)
        self.classifier = nn.Conv2d(self.feat_channels, self.num_anchors*self.cls_channels, 1)
        self.regressor = nn.Conv2d(self.feat_channels, self.num_anchors*4, 1)


    def init_weights(self):
        self.guided_anchor.init_weights()
        normal_init(self.conv, std=0.01)
        normal_init(self.classifier, std=0.01)
        normal_init(self.regressor, std=0.01)
        logging.info('Initialized weights for GARPNHead')

    def forward_ga(self, feats):
        return self.guided_anchor(feats)

    def forward_conv(self, adapt_feats):
        conv_xs = [self.relu(self.conv(af)) for af in adapt_feats]
        cls_outs = [self.classifier(cxs) for cxs in conv_xs]
        reg_outs = [self.regressor(cxs) for cxs in conv_xs]
        return cls_outs, reg_outs
        
    def forward(self, feats):
        loc_outs, shape_outs, shape_reformed_outs, adapt_feats = self.forward_ga(feats)
        cls_outs, reg_outs = self.forward_conv(adapt_feats)
        return cls_outs, reg_outs, loc_outs, shape_outs, shape_reformed_outs, adapt_feats

    def loss(self, cls_outs, reg_outs, loc_outs, shape_outs, shape_reformed_outs, adapt_feats,
             gt_bboxes, gt_labels, img_metas, cfg):
        print('Reached loss'.center(50, '*'))
        print('cls_outs:')
        debug.tensor_shape(cls_outs)
        print('reg_outs:')
        debug.tensor_shape(reg_outs)
        print('loc_outs:')
        debug.tensor_shape(loc_outs)
        print('shape_outs:')
        debug.tensor_shape(shape_outs)
        print('shape_reformed_outs:')
        debug.tensor_shape(shape_reformed_outs)
        print('adapt_feats:')
        debug.tensor_shape(adapt_feats)
        print('cfg')
        print(cfg)
        exit()
        return {}

    def predict_bboxes(cls_outs, reg_outs, loc_outs, shape_outs, shape_reformed_outs, adapt_feats, cfg):
        pass
        


    