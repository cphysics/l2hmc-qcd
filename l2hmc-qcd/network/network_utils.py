import numpy as np
import tensorflow as tf

import config as cfg
from config import TF_FLOATS, NP_FLOATS
from utils.seed_dict import seeds

np.random.seed(seeds['global_np'])
TF_FLOAT = TF_FLOATS[tf.keras.backend.floatx()]
NP_FLOAT = NP_FLOATS[tf.keras.backend.floatx()]

#  if '2.' not in tf.__version__:
#      tf.compat.v1.set_random_seed(seeds['global_tf'])


# pylint: disable=no-member

def tf_zeros(shape):
    """Return tensor of all zeros."""
    return tf.zeros(shape, dtype=TF_FLOAT)


def encode_angle(angle, method='cos_sin'):
    """Returns encoded angle using specified method.

    Args:
        angle (array-like): Input angles to encode.
        method (str): Encoding method used. Must be one of:
            `('binned', 'scaled', 'cos_sin', 'gaussian')`.
    Returns:
        x (array-like): Encoded angle. Same shape as `angle`.
    """
    if method == 'binned':  # 1-of-500 encoding
        x = np.zeros(500)
        x[int(round(250 * (angle / np.pi + 1))) % 500] = 1
    elif method == 'gaussian':  # Leaky binned encoding
        x = np.arange(500)
        idx = 250 * (angle / np.pi + 1)
        x = np.exp(-np.pi * (x - idx) ** 2)
    elif method == 'scaled':  # scaled to [-1, 1] encoding
        x = np.array([angle / np.pi])
    elif method == 'cos_sin':  # (cos(angle), sin(angle)) encoding
        x = np.array([np.cos(angle), np.sin(angle)])
    else:
        x = np.mod(angle, 2 * np.pi)

    return x


def decode_angle(arr, method='cos_sin'):
    """Returns decoded angle using specified method."""
    if method in ['binned', 'gaussian']:  # 1-of-500 or gaussian encoding
        m = np.max(arr)
        for idx, x in enumerate(arr):
            if abs(arr[idx] - m) < 1e-5:
                angle = np.pi * x / 250 - np.pi
                break
            angle = np.pi * np.dot(np.arange(500), arr) / 500  # averaging

    elif method == 'scaled':  # Scaled to [-1, 1] encoding
        angle = np.pi * arr[0]

    elif method == 'cos_sin':
        if tf.is_tensor(arr):
            angle = tf.atan2(arr[1], arr[0])
        else:
            angle = np.atan2(arr[1], arr[0])
    else:
        angle = arr

    return angle


def activation_model(model):
    """Create Keras Model that outputs activations of all conv./pool layers.

    Args:
        model (tf.keraas.Model): Model for which we wish to visualize
            activations.
    Returns:
        activation_model (tf.keras.Model): Model that outputs the activations
            for each layer in `model.
    """
    layer_outputs = [layer.output for layer in model.layers]

    output_model = tf.keras.models.Model(inputs=model.input,
                                         output=layer_outputs)

    return output_model


def flatten(_list):
    """Flatten nested list."""
    return [item for sublist in _list for item in sublist]


def add_elements_to_collection(elements, collection_list):
    """Add list of `elements` to `collection_list`."""
    elements = flatten(elements)
    collection_list = flatten(collection_list)
    #  collection_list = tf.nest.flatten(collection_list)
    for name in collection_list:
        collection = tf.get_collection_ref(name)
        collection_set = set(collection)
        for element in elements:
            if element not in collection_set:
                collection.append(element)


def _assign_moving_average(orig_val, new_val, momentum, name):
    """Assign moving average."""
    with tf.name_scope(name):
        scaled_diff = (1 - momentum) * (new_val - orig_val)
        return tf.assign_add(orig_val, scaled_diff)


def custom_dense(units, seed=None, factor=1., name=None, **kwargs):
    """Custom dense layer with specified weight intialization."""
    try:
        kernel_initializer = tf.keras.initializers.VarianceScaling(
            scale=2.*factor,
            mode='fan_in',
            distribution='truncated_normal',
            #  dtype=TF_FLOAT,
            seed=seed,
        )

    except AttributeError:
        kernel_initializer = tf.contrib.layers.variance_scaling_initializer(
            seed=seed,
            mode='FAN_IN',
            uniform=False,
            dtype=TF_FLOAT,
            factor=2.*factor,
        )

    bias_initializer = tf.constant_initializer(0.)  # , dtype=TF_FLOAT)

    return tf.keras.layers.Dense(
        units=units,
        name=name,
        use_bias=True,
        kernel_initializer=kernel_initializer,
        bias_initializer=bias_initializer,
        **kwargs
    )


def variable_on_cpu(name, shape, initializer):
    """Helper to create a Variable stored on CPU memory.

    Args:
        name: name of the variable
        shape: list of ints
        initializer: initializer for Variable

    Returns:
        Variable Tensor
    """
    with tf.device('/cpu:0'):
        var = tf.get_variable(name, shape, initializer, TF_FLOAT)
    return var


def variable_with_weight_decay(name, shape, stddev, weight_decay, cpu=True):
    """Helper to create an initialized Variable with weight decay.

    Note that the Variable is initialized with a truncated normal distribution.
    A weight decay is added only if one is specified.

    Args:
        name: Name of the variable
        shape: list of ints
        stddev: standard deviation of a truncated Gaussian
        wd: Add L2Loss weight decay multiplied by this float. If None, weight
            decay is not added for this variable.

    Returns:
        Variable Tensor
    """
    if cpu:
        var = variable_on_cpu(
            name, shape, tf.truncated_normal_initializer(stddev=stddev,
                                                         dtype=TF_FLOAT)
        )
    else:
        var = tf.get_variable(
            name, shape, tf.truncated_normal_initializer(stddev=stddev,
                                                         dtype=TF_FLOAT)
        )
    if weight_decay is not None:
        weight_decay = tf.multiply(tf.nn.l2_loss(var),
                                   weight_decay, name='weight_loss')
        tf.add_to_collection('losses', weight_decay)

    return var


def create_periodic_padding(samples, filter_size):
    """Create periodic padding for multiple samples, using filter_size."""
    original_size = tf.shape(samples)
    n = original_size[1]  # number of links in lattice
    padding = filter_size - 1
    samples = tf.reshape(samples, shape=(samples.shape[0], -1))

    x = []
    for sample in samples:
        padded = np.zeros((n + 2 * padding), n + 2 * padding, 2)
        # lower left corner
        padded[:padding, :padding, :] = sample[n-padding:, n-padding:, :]
        # lower middle
        padded[padding:n+padding, :padding, :] = sample[:, n-padding:, :]
        # loewr right corner
        padded[n+padding:, :padding, :] = sample[:padding, n-padding:, :]
        # left side
        padded[:padding, padding: n+padding, :] = sample[n-padding:, :, :]
        # center
        padded[:padding:n+padding, padding:n+padding, :] = sample[:, :, :]
        # right side
        padded[n+padding:, padding:n+padding:, :] = sample[:padding, :, :]
        # top middle
        padded[:padding:n+padding, n+padding:, :] = sample[:, :padding, :]
        # top right corner
        padded[n+padding:, n+padding:, :] = sample[:padding, :padding, :]

        x.append(padded)

    return np.array(x, dtype=NP_FLOAT).reshape(*original_size)
