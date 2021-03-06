TRAIN_ANN  = 'path_to_train_annotation'
TEST_ANN   = 'path_to_test_annotation'
TRAIN_IMGS = 'path_to_train_images'
TEST_IMGS  = 'path_to_test_images'

model=dict(
    type='RetinaNet',
    backbone=dict(type='ResNet', depth=50, frozen_stages=1, out_layers=(1, 2, 3, 4)),
    neck=dict(
        type='FPN',
        in_channels=[256, 512, 1024, 2048],
        out_channels=256,
        start_level=1,
        extra_use_convs=True,
        num_outs=5
    ),
    bbox_head=dict(
        type='RetinaHead',
        num_classes=21,
        in_channels=256,
        stacked_convs=4,
        feat_channels=256,
        octave_base_scale=4,
        scales_per_octave=3,
        anchor_ratios=[0.5, 1.0, 2.0],
        anchor_strides=[8, 16, 32, 64, 128],
        loss_cls=dict(
            type='FocalLoss', alpha=0.25, gamma=2.0, loss_weight=1.0),
        loss_bbox=dict(type='SmoothL1Loss', beta=1.0/9.0, loss_weight=1.0),
    )
)


train_cfg = dict(
    assigner=dict(
        type='MaxIoUAssigner',
        pos_iou=0.5,
        neg_iou=0.4,
        min_pos_iou=0.0
    ),

    allowed_border=-1,
    total_epochs=14,
    log_file='train.log',
    log_level='DEBUG'
)

test_cfg = dict(
    pre_nms=1000,
    min_bbox_size=0,
    min_score=0.05,
    nms_iou=0.5,
    nms_type='strict',
    max_per_img=100
) 

lr_config=dict(
    warmup_iters=500,
    warmup_ratio=0.001,
    lr_decay={9:0.1, 12:0.1},
)

optimizer=dict(type='SGD', lr=0.00125, momentum=0.9, weight_decay=0.0001)
optimizer_config=dict(grad_clip=dict(max_norm=35, norm_type=2))
#optimizer_config=dict(grad_clip=None)

ckpt_config=dict(interval=2)

img_norm = dict(
    mean=[123.675, 116.28, 103.53], std=[58.395, 57.12, 57.375], to_rgb=True)


train_pipeline=[
    dict(type='LoadImageFromFile'),
    dict(type='LoadAnnotations', with_bbox=True),
    dict(type='Resize', img_scale=(1333, 800), keep_ratio=True),
    dict(type='RandomFlip', flip_ratio=0.5),
    dict(type='Normalize', **img_norm),
    dict(type='Pad', size_divisor=32),
    dict(type='DefaultFormatBundle'),
    dict(type='Collect', keys=['img', 'gt_bboxes', 'gt_labels']),
]

test_pipeline=[
    dict(type='LoadImageFromFile'),
    dict(type='Resize', img_scale=(1333, 800), keep_ratio=True),
    dict(type='RandomFlip', flip_ratio=0.0),
    dict(type='Normalize', **img_norm),
    dict(type='Pad', size_divisor=32),
    dict(type='DefaultFormatBundle'),
    dict(type='Collect', keys=['img']),
]

data = dict(
    train=dict(
        imgs_per_gpu=2,
        ann_file=TRAIN_ANN,
        img_prefix=TRAIN_IMGS,
        pipeline=train_pipeline,
        loader=dict(batch_size=1, num_workers=4, shuffle=True),
    ),
    test=dict(
        imgs_per_gpu=2,
        ann_file=TEST_ANN,
        img_prefix=TEST_IMGS,
        pipeline=test_pipeline,
        loader=dict(batch_size=1, num_workers=4, shuffle=False),
    )
)

