"""
train_logger.py

Implements TrainLogger class responsible for saving/logging data from
GaugeModel.

Author: Sam Foreman (github: @saforem2)
Date: 04/09/2019
"""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import os

import numpy as np
import tensorflow as tf

import utils.file_io as io

from loggers.summary_utils import create_summaries
#  from .summary_utils import create_summaries

HSTR = ("{:^12s}" + 9 * "{:^10s}").format(
    "STEP", "t/STEP", "LOSS", "% ACC", "EPS",
    "BETA", "ACTION", "PLAQ", "(EXACT)", "LR"
)

DASH = (len(HSTR) + 1) * '-'
TRAIN_HEADER = DASH + '\n' + HSTR + '\n' + DASH

SKIP_KEYS = ['x_out', 'dx_proposed', 'dx_out']

class TrainLogger:
    """Responsible for file IO during training."""
    def __init__(self, model):
        self.model = model
        self.summaries = model.summaries
        self._keep_data = model.save_train_data
        self._clear_data = not self._keep_data
        self._print_steps = model.print_steps
        self._save_steps = model.save_steps
        self._logging_steps = model.logging_steps
        self._model_type = model._model_type
        self.train_data = {}
        self.h_strf = ("{:^13s}" + 9 * "{:^12s}").format(
            "STEP", "t/STEP", "LOSS", "% ACC", "EPS", "dx",
            "BETA", "LR", "exp(dH)", "sumlogdet",
        )

        if model._model_type == 'GaugeModel':
            self.obs_data = {}
            self.h_strf += ("{:^12s}").format("dQ (prop)")
            self.h_strf += ("{:^12s}").format("dQ (out)")
            self.h_strf += ("{:^12s}").format("plaq_err")

        self.dash = (len(self.h_strf) + 1) * '-'
        self.train_header = self.dash + '\n' + self.h_strf + '\n' + self.dash
        self.train_data_strings = [self.train_header]

        # log_dir will be None if using_hvd and hvd.rank() != 0
        # this prevents workers on different ranks from corrupting checkpoints
        #  if log_dir is not None and self.is_chief:
        dirs, files = self._create_dir_structure(model.log_dir)
        self.log_dir = dirs['log_dir']
        self.checkpoint_dir = dirs['checkpoint_dir']
        self.train_dir = dirs['train_dir']
        self.train_summary_dir = dirs['train_summary_dir']
        self.train_log_file = files['train_log_file']
        self.current_state_file = files['current_state_file']

        if self.summaries:
            self.writer = tf.summary.FileWriter(
                self.train_summary_dir, tf.compat.v1.get_default_graph()
            )
            self.summary_writer, self.summary_op = create_summaries(
                model, self.train_summary_dir, training=True
            )

    @staticmethod
    def _create_dir_structure(log_dir):
        """Create relevant directories for storing data.

        Args:
            log_dir: Root directory in which all other directories are created.
        """
        dirs = {
            'log_dir': log_dir,
            'train_dir': os.path.join(log_dir, 'training'),
            'checkpoint_dir': os.path.join(log_dir, 'checkpoints'),
            'train_summary_dir': os.path.join(log_dir, 'summaries', 'train'),
        }

        io.check_else_make_dir(list(dirs.values()))

        def _in_train_dir(fname):
            return os.path.join(dirs['train_dir'], fname)

        files = {
            'train_log_file': _in_train_dir('training_log.txt'),
            'current_state_file': _in_train_dir('current_state.z'),
        }

        return dirs, files

    def log_step(self, sess, data, net_weights):
        """Update self.logger.summaries."""
        feed_dict = {
            self.model.x: data['x_in'],
            self.model.beta: data['beta'],
            self.model.net_weights: net_weights,
            self.model.train_phase: True
        }
        summary_str = sess.run(self.summary_op, feed_dict=feed_dict)

        self.writer.add_summary(summary_str, global_step=data['step'])
        self.writer.flush()

    def _clear(self):
        self.train_data = {}

    def _update(self, data):
        for key, val in data.items():
            try:
                self.train_data[key].append(val)
            except KeyError:
                self.train_data[key] = [val]

    def update(self, sess, data, data_str, net_weights):
        """Update _current state and train_data."""
        step = data['step']
        if self._keep_data:
            self._update(data)

        if step % self._print_steps == 0:
            io.log(data_str)
            self.train_data_strings.append(data_str)

        if step % self._save_steps == 0:
            self.save_current_state(data)

        if self.summaries and (step + 1) % self._logging_steps == 0:
            self.log_step(sess, data, net_weights)

        if step % 100 == 0:
            io.log(self.train_header)

    def write_train_strings(self):
        """Write training strings out to file."""
        tlf = self.train_log_file
        _ = [io.write(s, tlf, 'a') for s in self.train_data_strings]

    def save_current_state(self, data):
        """Save curent state incase training needs to be restarted."""
        out_file = os.path.join(self.train_dir, 'current_state.z')
        io.savez(data, out_file, name='current_state')

    def load_train_data(self):
        """Load existing training data from `self.train_dir`."""
        data_file = os.path.join(self.train_dir, 'train_data.z')
        try:
            train_data = io.loadz(data_file)
        except FileNotFoundError:
            io.log(f'Unable to load training data from: {data_file}.')
            train_data = {}

        return train_data

    def restore_train_data(self):
        """If restarting training, load in previous training data."""
        self.train_data = self.load_train_data()

    def save_train_data(self, out_file=None):
        """Save train data to `.z` file."""
        if out_file is None:
            out_dir = os.path.join(self.train_dir, 'train_data')
            io.check_else_make_dir(out_dir)
            for key, val in self.train_data.items():
                if key == 'x_in':
                    continue
                out_file = os.path.join(out_dir, f'{key}.z')
                io.savez(np.array(val), out_file, name=key)
        else:
            io.log(f'Saving train data to: {out_file}.')
            io.savez(self.train_data, out_file, name='train_data')
