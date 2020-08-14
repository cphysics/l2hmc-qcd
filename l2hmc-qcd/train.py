"""
train.py

Train 2D U(1) model using eager execution in tensorflow.
"""
from __future__ import absolute_import, division, print_function

import os
import sys
import logging

import tensorflow as tf

import utils.file_io as io

from utils import DummyTqdmFile
from utils.attr_dict import AttrDict
from utils.parse_args import parse_args
from utils.training_utils import train
from utils.inference_utils import run


def main(args, log_file=None):
    """Main method for training."""
    #  io.log(TRAIN_STR)
    tf.keras.backend.set_floatx('float32')
    x, dynamics, train_data, args = train(args, md_steps=100,
                                          log_file=log_file)
    if args.run_steps > 0:
        dynamics, run_data, x = run(dynamics, args, x=x)
        # run again with random start
        dynamics, run_data, x = run(dynamics, args)

    return x, dynamics, train_data, run_data, args


if __name__ == '__main__':
    FLAGS = parse_args()
    FLAGS = AttrDict(FLAGS.__dict__)
    #  LEVEL = FLAGS.get('logging_level', 'DEBUG').upper()

    #  logging.basicConfig(
    #      level=io.LOG_LEVELS[LEVEL],  # Defaults to INFO
    #      format="%(asctime)s:%(levelname)s:%(message)s",
    #      stream=DummyTqdmFile(sys.stderr)
    #  )

    LOG_FILE = os.path.join(os.getcwd(), 'log_dirs.txt')
    _ = main(FLAGS, LOG_FILE)
