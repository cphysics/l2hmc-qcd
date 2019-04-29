"""
train_logger.py

Implements TrainLogger class responsible for saving/logging data from
GaugeModel.

Author: Sam Foreman (github: @saforem2)
Date: 04/09/2019
"""
import os
import pickle

from scipy.stats import sem
import utils.file_io as io

from globals import FILE_PATH, TRAIN_HEADER
from lattice.lattice import u1_plaq_exact
from utils.tf_logging import variable_summaries

import numpy as np

import tensorflow as tf


def save_params(params, log_dir):
    io.check_else_make_dir(log_dir)
    params_txt_file = os.path.join(log_dir, 'parameters.txt')
    params_pkl_file = os.path.join(log_dir, 'parameters.pkl')
    with open(params_txt_file, 'w') as f:
        for key, val in params.items():
            f.write(f"{key}: {val}\n")
    with open(params_pkl_file, 'wb') as f:
        pickle.dump(params, f)


class TrainLogger:
    def __init__(self, sess, model, log_dir, summaries=False):
        self.sess = sess
        self.model = model
        self.summaries = summaries

        self.charges_dict = {}
        self.charge_diffs_dict = {}

        self._current_state = {
            'step': 0,
            'beta': self.model.beta_init,
            'eps': self.model.eps,
            'lr': self.model.lr_init,
            'samples': self.model.samples,
        }

        self.train_data_strings = [TRAIN_HEADER]
        self.train_data = {
            'loss': {},
            'actions': {},
            'plaqs': {},
            'charges': {},
            'charge_diffs': {},
            'accept_probs': {}
        }

        # log_dir will be None if using_hvd and hvd.rank() != 0
        # this prevents workers on different ranks from corrupting checkpoints
        #  if log_dir is not None and self.is_chief:
        self._create_dir_structure(log_dir)
        save_params(self.model.params, self.log_dir)

        if self.summaries:
            self.writer = tf.summary.FileWriter(self.train_summary_dir,
                                                self.sess.graph)
            self.create_summaries()

    def _create_dir_structure(self, log_dir):
        """Create relevant directories for storing data."""
        project_dir = os.path.abspath(os.path.dirname(FILE_PATH))
        root_log_dir = os.path.join(project_dir, log_dir)
        io.check_else_make_dir(root_log_dir)

        run_num = io.get_run_num(root_log_dir)
        log_dir = os.path.abspath(os.path.join(root_log_dir,
                                               f'run_{run_num}'))
        io.check_else_make_dir(log_dir)

        self.log_dir = log_dir
        self.train_dir = os.path.join(self.log_dir, 'training')
        self.checkpoint_dir = os.path.join(self.log_dir, 'checkpoints')
        self.train_summary_dir = os.path.join(
            self.log_dir, 'summaries', 'train'
        )

        self.train_log_file = os.path.join(self.train_dir, 'training_log.txt')
        self.current_state_file = os.path.join(self.train_dir,
                                               'current_state.pkl')

        io.make_dirs([self.train_dir,
                      self.train_summary_dir,
                      self.checkpoint_dir])

    def _add_loss_summaries(self, total_loss):
        """Add summaries for losses in GaugeModel.

        Generates a moving average for all losses and associated summaries for
        visualizing the performance of the network.

        Args:
            total_loss: Total loss from model._calc_loss()

        Returns:
            loss_averages_op: Op for generating moving averages of losses.
        """
        # Compute the moving average of all individual losses and the total
        # loss.
        loss_averages = tf.train.ExponentialMovingAverage(0.9, name='avg')
        losses = tf.get_collection('losses')
        loss_averages_op = loss_averages.apply(losses + [total_loss])

        # Attach a scalar summary to all individual losses and the total loss;
        # do the same for the averaged version of the losses.
        for l in losses + [total_loss]:
            # Name each loss as '(raw)' and name the moving average version of
            # the loss as the original loss name.
            tf.summary.scalar(l.op.name + ' (raw)', l)
            tf.summary.scalar(l.op.name, loss_averages.average(l))

        return loss_averages_op

    def create_summaries(self):
        """Create summary objects for logging in TensorBoard."""
        ld = self.log_dir
        self.summary_writer = tf.contrib.summary.create_file_writer(ld)

        grads_and_vars = zip(self.model.grads,
                             self.model.dynamics.trainable_variables)

        with tf.name_scope('loss'):
            tf.summary.scalar('loss', self.model.loss_op)

        #  self.loss_averages_op = self._add_loss_summaries(self.model.loss_op)

        with tf.name_scope('learning_rate'):
            tf.summary.scalar('learning_rate', self.model.lr)

        with tf.name_scope('step_size'):
            tf.summary.scalar('step_size', self.model.dynamics.eps)

        with tf.name_scope('tunneling_events'):
            tf.summary.scalar('tunneling_events_per_sample',
                              self.model.charge_diffs_op)

        with tf.name_scope('avg_plaq'):
            tf.summary.scalar('avg_plaq', self.model.avg_plaqs_op)

        for var in tf.trainable_variables():
            if 'batch_normalization' not in var.op.name:
                tf.summary.histogram(var.op.name, var)

        with tf.name_scope('summaries'):
            for grad, var in grads_and_vars:
                try:
                    layer, _type = var.name.split('/')[-2:]
                    name = layer + '_' + _type[:-2]
                except (AttributeError, IndexError):
                    name = var.name[:-2]

                if 'batch_norm' not in name:
                    variable_summaries(var, name)
                    variable_summaries(grad, name + '/gradients')
                    tf.summary.histogram(name + '/gradients', grad)

        self.summary_op = tf.summary.merge_all(name='summary_op')

    def save_current_state(self):
        """Save current state to pickle file.

        The current state contains the following, which makes life easier if
        we're trying to restore training from a saved checkpoint: 
            * most recent samples
            * learning_rate
            * beta
            * dynamics.eps
            * training_step
        """
        with open(self.current_state_file, 'wb') as f:
            pickle.dump(self._current_state, f)

    def log_step(self, step, samples_np, beta_np):
        """Update self.logger.summaries."""
        feed_dict = {
            self.model.x: samples_np,
            self.model.beta: beta_np
        }
        summary_str = self.sess.run(self.summary_op,
                                    feed_dict=feed_dict)

        self.writer.add_summary(summary_str, global_step=step)
        self.writer.flush()

    def update_training(self, data, data_str):
        """Update _current state and train_data."""
        step = data['step']
        beta = data['beta']
        self._current_state['step'] = step
        self._current_state['beta'] = beta
        self._current_state['lr'] = data['lr']
        self._current_state['eps'] = data['eps']
        self._current_state['samples'] = data['samples']

        key = (step, beta)

        self.charges_dict[key] = data['charges']
        self.charge_diffs_dict[key] = data['charge_diffs']

        self.train_data['loss'][key] = data['loss']
        self.train_data['actions'][key] = data['actions']
        self.train_data['plaqs'][key] = data['plaqs']
        self.train_data['charges'][key] = data['charges']
        self.train_data['charge_diffs'][key] = data['charge_diffs']
        self.train_data['accept_probs'][key] = data['px']

        self.train_data_strings.append(data_str)
        if step % self.model.print_steps == 0:
            io.log(data_str)

        if self.summaries and (step + 1) % self.model.logging_steps == 0:
            self.log_step(step, data['samples'], beta)

        if (step + 1) % self.model.save_steps == 0:
            self.model.save(self.sess, self.checkpoint_dir)
            self.save_current_state()

        if step % 100 == 0:
            io.log(TRAIN_HEADER)

    def write_train_strings(self):
        """Write training strings out to file."""
        tlf = self.train_log_file
        _ = [io.write(s, tlf, 'a') for s in self.train_data_strings]