# yapf:disable
log_config = dict(
    interval=50,
    hooks=[
        dict(type='TextLoggerHook'),
        # dict(type='TensorboardLoggerHook')
    ])
# yapf:enable
checkpoint_config = dict(interval=4000)
evaluation = dict(by_epoch=False, interval=2000, metric='accuracy')
dist_params = dict(backend='nccl')
log_level = 'INFO'
load_from = None
resume_from = None
workflow = [('train', 1)]
pin_memory = True
seed = 1234567
