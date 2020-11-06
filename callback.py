import os
import six
import yaml
import numpy as np
import pandas as pd
import tensorflow as tf
from tensorflow.keras.callbacks import ModelCheckpoint
from tensorflow.keras.callbacks import CSVLogger
from tensorflow.keras.callbacks import TensorBoard
from tensorflow.python.keras.utils import tf_utils
from tensorflow.python.platform import tf_logging as logging

from common import create_stamp


class OptionalLearningRateSchedule(tf.keras.optimizers.schedules.LearningRateSchedule):
    def __init__(self, args, steps_per_epoch, initial_epoch):
        super(OptionalLearningRateSchedule, self).__init__()
        self.args = args
        self.steps_per_epoch = steps_per_epoch
        self.initial_epoch = initial_epoch

        if self.args.lr_mode == 'exponential':
            decay_epochs = [int(e) for e in self.args.lr_interval.split(',')]
            lr_values = [self.args.lr * (self.args.lr_value ** k)for k in range(len(decay_epochs) + 1)]
            self.lr_scheduler = \
                tf.keras.optimizers.schedules.PiecewiseConstantDecay(decay_epochs, lr_values)

        elif self.args.lr_mode == 'cosine':
            self.lr_scheduler = \
                tf.keras.experimental.CosineDecay(self.args.lr, self.args.epochs)

        elif self.args.lr_mode == 'constant':
            self.lr_scheduler = lambda x: self.args.lr
            

    def get_config(self):
        return {
            'steps_per_epoch': self.steps_per_epoch,
            'init_lr': self.args.lr,
            'lr_mode': self.args.lr_mode,
            'lr_value': self.args.lr_value,
            'lr_interval': self.args.lr_interval,}

    def __call__(self, step):
        step = tf.cast(step, tf.float32)
        step += self.initial_epoch * self.steps_per_epoch
        lr_epoch = (step / self.steps_per_epoch)
        if self.args.lr_mode == 'constant':
            return self.args.lr
        else:
            return self.lr_scheduler(lr_epoch)


class MocoModelCheckpoint(ModelCheckpoint):
    def save_queue(self, filepath, epoch):
        filepath_add = filepath.replace('checkpoint', 'checkpoint/queue')
        filepath_add = filepath_add.replace('.h5', '.npy')
        np.save(filepath_add, self.model.queue.numpy())
        print('\nEpoch %05d: saving queue to %s' % (epoch + 1, filepath_add))
        
    def on_epoch_end(self, epoch, logs=None):
        self.epochs_since_last_save += 1
        if self.save_freq == 'epoch':
            logs = logs or {}
            if isinstance(self.save_freq, 
                          int) or self.epochs_since_last_save >= self.period:
                logs = tf_utils.to_numpy_or_python_type(logs)
                self.epochs_since_last_save = 0
                filepath = self._get_file_path(epoch, logs)
                
                self.save_queue(filepath, epoch)
                for m in self.model.layers:
                    try:
                        if '_q_' in m.name:
                            model_name = 'query'
                        elif '_k_' in m.name:
                            model_name = 'key'
                        else:
                            raise ValueError()

                        filepath_add = filepath.replace('checkpoint', f'checkpoint/{model_name}')
                        if self.save_best_only:
                            current = logs.get(self.monitor)
                            if current is None:
                                logging.warning('Can save best %s only with %s available, '
                                                'skipping.' % (model_name, self.monitor))
                            else:
                                if self.monitor_op(current, self.best):
                                    if self.verbose > 0:
                                        print('\nEpoch %05d: %s improved from %0.5f to %0.5f,'
                                              ' saving %s to %s' % (epoch + 1, self.monitor, self.best, 
                                                                    current, model_name, filepath_add))
                                    self.best = current
                                    if self.save_weights_only:
                                        m.save_weights(
                                            filepath_add, overwrite=True, options=self._options)
                                    else:
                                        m.save(filepath_add, overwrite=True, options=self._options)
                                else:
                                    if self.verbose > 0:
                                        print('\nEpoch %05d: %s did not improve from %0.5f' %
                                            (epoch + 1, self.monitor, self.best))
                        else:
                            if self.verbose > 0:
                                print('\nEpoch %05d: saving %s to %s' % (epoch + 1, model_name, filepath_add))
                            if self.save_weights_only:
                                m.save_weights(
                                    filepath_add, overwrite=True, options=self._options)
                            else:
                                m.save(filepath_add, overwrite=True, options=self._options)

                        self._maybe_remove_file()
                    except IOError as e:
                        # `e.errno` appears to be `None` so checking the content of `e.args[0]`.
                        if 'is a directory' in six.ensure_str(e.args[0]).lower():
                            raise IOError('Please specify a non-directory filepath for '
                                        'ModelCheckpoint. Filepath used is an existing '
                                        'directory: {}'.format(filepath))


def create_callbacks(args, metrics=None):
    if args.snapshot is None:
        if args.checkpoint or args.history or args.tensorboard:
            flag = True
            while flag:
                try:
                    os.makedirs(f'{args.result_path}/{args.task}/{args.stamp}')
                    flag = False
                except:
                    args.stamp = create_stamp()

            yaml.dump(
                vars(args), 
                open(f'{args.result_path}/{args.task}/{args.stamp}/model_desc.yml', 'w'), 
                default_flow_style=False)

    callbacks = []
    if args.checkpoint:
        for m in ['query', 'key', 'queue']:
            os.makedirs(f'{args.result_path}/{args.task}/{args.stamp}/checkpoint/{m}', exist_ok=True)
            
        callbacks.append(MocoModelCheckpoint(
            filepath=os.path.join(
                f'{args.result_path}/{args.task}/{args.stamp}/checkpoint',
                '{epoch:04d}_{loss:.4f}_{acc1:.4f}_{acc5:.4f}.h5'),
            monitor='acc1',
            mode='max',
            verbose=1,
            save_weights_only=True))

    if args.history:
        os.makedirs(f'{args.result_path}/{args.task}/{args.stamp}/history', exist_ok=True)
        callbacks.append(CSVLogger(
            filename=f'{args.result_path}/{args.task}/{args.stamp}/history/epoch.csv',
            separator=',', append=True))

    if args.tensorboard:
        callbacks.append(TensorBoard(
            log_dir=f'{args.result_path}/{args.task}/{args.stamp}/logs',
            histogram_freq=args.tb_histogram,
            write_graph=True, 
            write_images=True,
            update_freq=args.tb_interval,
            profile_batch=100,))

    return callbacks