import torch
import logging


# often changed configs
LR = None
MAX_EPOCHS = 14
SAVE_INTERVAL = 2

train_data_cfg = dict(
    img_dir='/home/server2/4T/liyiqing/dataset/PASCAL_VOC_07/voc2007_trainval/VOC2007/JPEGImages',
    json='/home/server2/4T/liyiqing/dataset/PASCAL_VOC_07/voc2007_trainval/voc2007_trainval.json',
    img_size=(1000,600),
    img_norm=dict(mean=[0.390, 0.423, 0.446], std=[0.282, 0.270, 0.273]),
    loader_cfg=dict(batch_size=1, num_workers=2, shuffle=True),
)

test_data_cfg = dict(
    img_dir='/home/server2/4T/liyiqing/dataset/PASCAL_VOC_07/voc2007_test/VOC2007/JPEGImages',
    loader_cfg=dict(batch_size=1, num_workers=2, shuffle=True),
)


model = dict(
    num_classes=20,
    anchor_scales=[256],
    anchor_aspect_ratios=[1.0],
    anchor_pos_iou=0.7,
    anchor_neg_iou=0.3,
    anchor_max_pos=128,
    anchor_max_targets=256,
    train_props_pre_nms=12000,
    train_props_post_nms=2000,
    train_props_nms_iou=0.7,
    test_props_pre_nms=6000,
    test_props_post_nms=300,
    test_props_nms_iou=0.5,
    props_pos_iou=0.5,
    props_neg_iou_hi=0.5,
    props_neg_iou_lo=0.1,
    props_max_pos=32,
    props_max_targets=128,
    roi_pool_size=(7, 7),
    transfer_rcnn_fc=True
)


train_cfg = dict(
    max_epochs=MAX_EPOCHS,
    optim=torch.optim.Adam,
    optim_kwargs=dict(lr=LR),
    rpn_loss_lambda=1.0,
    rcnn_loss_lambda=1.0,
    loss_lambda=1.0,
    log_file='train_20epochs.log',
    lr_scheduler=lambda e : 0.00001,
    log_level=logging.DEBUG,
    device=torch.device('cpu'),
    save_interval=2,
    rpn_only=False
)


test_cfg = dict(
    min_score=0.05
)