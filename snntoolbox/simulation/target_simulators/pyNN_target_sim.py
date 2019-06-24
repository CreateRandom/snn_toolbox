# -*- coding: utf-8 -*-
"""
Building and simulating spiking neural networks using
`pyNN <http://neuralensemble.org/docs/PyNN/>`_.

@author: rbodo
"""

from __future__ import division, absolute_import
from __future__ import print_function, unicode_literals

import os
import sys
import warnings

from importlib import import_module
import numpy as np
from future import standard_library
from six.moves import cPickle

from snntoolbox.utils.utils import confirm_overwrite
from snntoolbox.simulation.utils import AbstractSNN
from snntoolbox.bin.utils import config_string_to_set_of_strings
from pyNN.utility import ProgressBar
from textwrap import fill

standard_library.install_aliases()


class SNN(AbstractSNN):
    """Class to hold the compiled spiking neural network.

    Represents the compiled spiking neural network, ready for testing in a
    spiking simulator.

    Attributes
    ----------

    layers: list[pyNN.Population]
        Each entry represents a layer, i.e. a population of neurons, in form of
        pyNN ``Population`` objects.

    connections: list[pyNN.Projection]
        pyNN ``Projection`` objects representing the connections between
        individual layers.

    cellparams: dict
        Neuron cell parameters determining properties of the spiking neurons in
        pyNN simulators.
    """

    def __init__(self, config, queue=None):

        AbstractSNN.__init__(self, config, queue)

        self.layers = []
        self.connections = []
        self.cellparams = {key: config.getfloat('cell', key) for key in
                           config_string_to_set_of_strings(config.get(
                               'restrictions', 'cellparams_pyNN'))}
        if 'i_offset' in self.cellparams.keys():
            print("SNN toolbox WARNING: The cell parameter 'i_offset' is "
                  "reserved for the biases and should not be set globally.")
            self.cellparams.pop('i_offset')

        self.change_padding = False

        self.output_script_path = os.path.join(
            self.config.get('paths', 'path_wd'), '{}.py'.format(
                self.config.get('paths', 'filename_snn')))

    @property
    def is_parallelizable(self):
        return False

    def add_input_layer(self, input_shape):

        celltype = self.sim.SpikeSourcePoisson() if self._poisson_input \
            else self.sim.SpikeSourceArray()
        self.layers.append(self.sim.Population(
            np.asscalar(np.prod(input_shape[1:], dtype=np.int)), celltype,
            label='InputLayer'))
        
        lines = [
            '"""\n',
            fill("This pyNN script was automatically generated by the SNN "
                 "toolbox (https://snntoolbox.readthedocs.io)."),
            "\n",
            fill("The script is the result of loading and parsing an ANN "
                 "model, and converting it into an SNN. Run this script to "
                 "instantiate an SNN that can be simulated with pyNN. The SNN "
                 "toolbox settings used to generate this script are contained "
                 "in the config file in {}."
                 "\n".format(self.config.get('paths', 'path_wd'))),
            '\n"""',
            "\n\n",
            "import os\n",
            "import numpy as np\n",
            "from importlib import import_module\n",
            "from snntoolbox.bin.utils import config_string_to_set_of_strings",
            "\n",
            "try:\n",
            "\timport configparser\n",
            "except ImportError:\n",
            "\timport ConfigParser as configparser\n",
            "\tconfigparser = configparser\n",
            "\n\n\n",
            "def get_model():\n",
            "\t# Load snntoolbox configuration. The '.config' file read here \n"
            "\t# differs from the 'config' file read by the toolbox in two \n"
            "\t# ways: (1) '.config' complements the 'config' file by all \n"
            "\t# default settings which were not explicitly specified by the \n"
            "\t# user. (2) The toolbox may have overridden some settings \n"
            "\t# during conversion. Check the console output of the toolbox \n"
            "\t# for respective messages.\n",
            "\tfilepath = '{}'\n".format(os.path.join(self.config.get(
                'paths', 'log_dir_of_current_run'), '.config')),
            "\tconfig = configparser.ConfigParser()\n",
            "\tconfig.read(filepath)\n",
            "\n",
            "\t# Define path to where the network connections are stored.\n",
            "\tpath_wd = config.get('paths', 'path_wd')\n",
            "\n",
            "\t# Define cell parameters.\n",
            "\tcellparams = {key: config.getfloat('cell', key) for key in "
            "config_string_to_set_of_strings(config.get('restrictions', "
            "'cellparams_pyNN'))}\n",
            "\n",
            "\t# Configure simulator.\n",
            "\tsimulator = config.get('simulation', 'simulator')\n",
            "\tdt = config.getfloat('simulation', 'dt')\n",
            "\tsim = import_module('pyNN.{}'.format(simulator))\n",
            "\tsim.setup(timestep=dt)\n",
            "\n",
            "\t# Build the network.",
            "\n",
            "\tlayers = []\n",
            "\n",
            "\t# Define input layer.\n",
            "\tuse_poisson_input = config.getboolean('input', 'poisson_input')"
            "\n",
            "\tcelltype = sim.SpikeSourcePoisson() if use_poisson_input else "
            "sim.SpikeSourceArray()\n",
            "\tlayers.append(sim.Population(np.asscalar(np.prod({}, "
            "dtype=np.int)), celltype, label='InputLayer'))\n"
            "".format(input_shape[1:]),
            "\n",
            "\t# Add remaining layers.\n"
        ]
        with open(self.output_script_path, 'w') as f:
            f.writelines(lines)

    def add_layer(self, layer):
        
        # This implementation of ZeroPadding layers assumes symmetric single
        # padding ((1, 1), (1, 1)).
        # Todo: Generalize for asymmetric padding or arbitrary size.
        if 'ZeroPadding' in layer.__class__.__name__:
            # noinspection PyUnresolvedReferences
            padding = layer.padding
            if set(padding).issubset((1, (1, 1))):
                self.change_padding = True
                return
            else:
                raise NotImplementedError(
                    "Border_mode {} not supported.".format(padding))

        if 'Flatten' in layer.__class__.__name__:
            self.flatten_shapes.append(
                (layer.name, get_shape_from_label(self.layers[-1].label)))
            return

        self.layers.append(self.sim.Population(
            np.asscalar(np.prod(layer.output_shape[1:], dtype=np.int)),
            self.sim.IF_curr_exp, self.cellparams, label=layer.name))

        self.layers[-1].initialize(v=self.layers[-1].get('v_rest'))

        lines = [
            "\n",
            "\t# Add layer {}.\n".format(layer.name),
            "\tprint('Building layer {}.')\n".format(layer.name),
            "\tlayers.append(sim.Population(np.asscalar(np.prod({}, "
            "dtype=np.int)), sim.IF_curr_exp, cellparams, label='{}'))\n"
            "".format(layer.output_shape[1:], layer.name),
            "\tlayers[-1].initialize(v=layers[-1].get('v_rest'))\n"
        ]
        with open(self.output_script_path, 'a') as f:
            f.writelines(lines)

    def build_dense(self, layer):
        """

        Parameters
        ----------
        layer : keras.layers.Dense

        Returns
        -------

        """

        if layer.activation.__name__ == 'softmax':
            warnings.warn("Activation 'softmax' not implemented. Using 'relu' "
                          "activation instead.", RuntimeWarning)

        weights, biases = layer.get_weights()

        self.set_biases(np.array(biases, 'float64'))
        delay = self.config.getfloat('cell', 'delay')
        connections = []
        if len(self.flatten_shapes) == 1:
            print("Swapping data_format of Flatten layer.")
            flatten_name, shape = self.flatten_shapes.pop()
            if self.data_format == 'channels_last':
                y_in, x_in, f_in = shape
            else:
                f_in, y_in, x_in = shape
            for i in range(weights.shape[0]):  # Input neurons
                # Sweep across channel axis of feature map. Assumes that each
                # consecutive input neuron lies in a different channel. This is
                # the case for channels_last, but not for channels_first.
                f = i % f_in
                # Sweep across height of feature map. Increase y by one if all
                # rows along the channel axis were seen.
                y = i // (f_in * x_in)
                # Sweep across width of feature map.
                x = (i // f_in) % x_in
                new_i = f * x_in * y_in + x_in * y + x
                for j in range(weights.shape[1]):  # Output neurons
                    connections.append((new_i, j, weights[i, j], delay))
        elif len(self.flatten_shapes) > 1:
            raise RuntimeWarning("Not all Flatten layers have been consumed.")
        else:
            for i in range(weights.shape[0]):
                for j in range(weights.shape[1]):
                    connections.append((i, j, weights[i, j], delay))

        if self.config.getboolean('tools', 'simulate'):
            self.connections.append(self.sim.Projection(
                self.layers[-2], self.layers[-1],
                self.sim.FromListConnector(connections, ['weight', 'delay'])))

        lines = [
            "\n",
            "\t# Load dense projections created by snntoolbox.\n",
            "\tfilepath = os.path.join(path_wd, layers[-1].label)\n",
            "\tsim.Projection(layers[-2], layers[-1], sim.FromFileConnector("
            "filepath))\n",
            "\n",
            "\t# Set biases.\n",
            "\tfilepath = os.path.join(path_wd, layers[-1].label + '_biases')"
            "\n",
            "\tbiases = np.loadtxt(filepath)\n",
            "\tlayers[-1].set(i_offset=biases*dt/1e2)\n"
        ]
        with open(self.output_script_path, 'a') as f:
            f.writelines(lines)

    def build_convolution(self, layer):
        from snntoolbox.simulation.utils import build_convolution

        # If the parsed model contains a ZeroPadding layer, we need to tell the
        # Conv layer about it here, because ZeroPadding layers are removed when
        # building the pyNN model.
        if self.change_padding:
            if layer.padding == 'valid':
                self.change_padding = False
                layer.padding = 'ZeroPadding'
            else:
                raise NotImplementedError(
                    "Border_mode {} in combination with ZeroPadding is not "
                    "supported.".format(layer.padding))

        delay = self.config.getfloat('cell', 'delay')
        transpose_kernel = \
            self.config.get('simulation', 'keras_backend') == 'tensorflow'
        connections, biases = build_convolution(layer, delay, transpose_kernel)

        self.set_biases(biases)

        if self.config.getboolean('tools', 'simulate'):
            self.connections.append(self.sim.Projection(
                self.layers[-2], self.layers[-1],
                self.sim.FromListConnector(connections, ['weight', 'delay'])))

        lines = [
            "\n",
            "\t# Load convolution projections created by snntoolbox.\n",
            "\tfilepath = os.path.join(path_wd, layers[-1].label)\n",
            "\tsim.Projection(layers[-2], layers[-1], sim.FromFileConnector("
            "filepath))\n",
            "\n",
            "\t# Set biases.\n",
            "\tfilepath = os.path.join(path_wd, layers[-1].label + '_biases')"
            "\n",
            "\tbiases = np.loadtxt(filepath)\n",
            "\tlayers[-1].set(i_offset=biases*dt/1e2)\n"
        ]
        with open(self.output_script_path, 'a') as f:
            f.writelines(lines)

    def build_pooling(self, layer):
        from snntoolbox.simulation.utils import build_pooling

        delay = self.config.getfloat('cell', 'delay')
        connections = build_pooling(layer, delay)
        if self.config.getboolean('tools', 'simulate'):
            self.connections.append(self.sim.Projection(
                self.layers[-2], self.layers[-1],
                self.sim.FromListConnector(connections, ['weight', 'delay'])))

        lines = [
            "\n",
            "\t# Load pooling projections created by snntoolbox.\n",
            "\tfilepath = os.path.join(path_wd, layers[-1].label)\n",
            "\tsim.Projection(layers[-2], layers[-1], sim.FromFileConnector("
            "filepath))\n"
        ]
        with open(self.output_script_path, 'a') as f:
            f.writelines(lines)

    def compile(self):

        lines = [
            "\n",
            "\tprint('Finished building pyNN model.')\n",
            "\n",
            "\treturn sim, layers\n",
            "\n",
            "# pyNN script compilation complete.\n"
        ]
        with open(self.output_script_path, 'a') as f:
            f.writelines(lines)

    def simulate(self, **kwargs):

        data = kwargs[str('x_b_l')]
        if self.data_format == 'channels_last' and data.ndim == 4:
            data = np.moveaxis(data, 3, 1)

        x_flat = np.ravel(data)
        if self._poisson_input:
            self.layers[0].set(rate=list(x_flat / self.rescale_fac * 1000))
        elif self._dataset_format == 'aedat':
            raise NotImplementedError
        else:
            spike_times = \
                [np.linspace(0, self._duration, self._duration * amplitude)
                 for amplitude in x_flat]
            self.layers[0].set(spike_times=spike_times)

        from pynn_object_serialisation.functions import intercept_simulator
        import pylab
        current_time = pylab.datetime.datetime.now().strftime("_%H%M%S_%d%m%Y")
        intercept_simulator(self.sim, "snn_toolbox_pynn_" + current_time)

        self.sim.run(self._duration - self._dt,
                     callbacks=[MyProgressBar(self._dt, self._duration)])
        print("\nCollecting results...")
        output_b_l_t = self.get_recorded_vars(self.layers)

        return output_b_l_t

    def reset(self, sample_idx):

        mod = self.config.getint('simulation', 'reset_between_nth_sample')
        mod = mod if mod else sample_idx + 1
        if sample_idx % mod == 0:
            print("Resetting simulator...")
            self.sim.reset()
            print("Done.")

    def end_sim(self):

        self.sim.end()

    def save(self, path, filename):

        print("Saving model to {}...".format(path))
        self.save_assembly(path, filename)
        self.save_connections(path)
        self.save_biases(path)
        print("Done.\n")

    def load(self, path, filename):

        if os.path.exists(self.output_script_path):
            self.sim, self.layers = get_snn_from_script(self.output_script_path)
            return

        self.layers = self.load_assembly(path, filename)
        for i in range(len(self.layers) - 1):
            filepath = os.path.join(path, self.layers[i + 1].label)
            assert os.path.isfile(filepath), \
                "Connections were not found at specified location."
            self.sim.Projection(self.layers[i], self.layers[i + 1],
                                self.sim.FromFileConnector(filepath))
            self.layers[i + 1].set(**self.cellparams)
            self.layers[i + 1].initialize(v=self.layers[i + 1].get('v_rest'))
            # Biases should be already be loaded from the assembly file.
            # Otherwise do this:
            # filepath = os.path.join(path, self.layers[i + 1].label+'_biases')
            # biases = np.loadtxt(filepath)
            # self.layers[i + 1].set(i_offset=biases*self._dt/1e2)

    def init_cells(self):

        vars_to_record = self.get_vars_to_record()

        if 'spikes' in vars_to_record:
            self.layers[0].record([str('spikes')])  # Input layer has no 'v'

        for layer in self.layers[1:]:
            layer.record(vars_to_record)

        # The spikes of the last layer are recorded by default because they
        # contain the networks output (classification guess).
        if 'spikes' not in vars_to_record:
            vars_to_record.append(str('spikes'))
            self.layers[-1].record(vars_to_record)

    def set_biases(self, biases):
        """Set biases.

        Notes
        -----

        This has not been tested yet.
        """

        if not np.any(biases):
            return

        self.layers[-1].set(i_offset=biases*self._dt/1e2)

    def get_vars_to_record(self):
        """Get variables to record during simulation.

        Returns
        -------

        vars_to_record: list[str]
            The names of variables to record during simulation.
        """

        vars_to_record = []

        if any({'spiketrains', 'spikerates', 'correlation', 'spikecounts',
                'hist_spikerates_activations'} & self._plot_keys) \
                or 'spiketrains_n_b_l_t' in self._log_keys:
            vars_to_record.append(str('spikes'))

        if 'mem_n_b_l_t' in self._log_keys or 'v_mem' in self._plot_keys:
            vars_to_record.append(str('v'))

        return vars_to_record

    def get_spiketrains(self, **kwargs):
        j = self._spiketrains_container_counter
        if self.spiketrains_n_b_l_t is None \
                or j >= len(self.spiketrains_n_b_l_t):
            return None

        shape = self.spiketrains_n_b_l_t[j][0].shape

        # Outer for-loop that calls this function starts with
        # 'monitor_index' = 0, but this is reserved for the input and handled by
        # `get_spiketrains_input()`.
        i = len(self.layers) - 1 if kwargs[str('monitor_index')] == -1 else \
            kwargs[str('monitor_index')] + 1
        spiketrains_flat = self.layers[i].get_data().segments[-1].spiketrains
        spiketrains_b_l_t = self.reshape_flattened_spiketrains(spiketrains_flat,
                                                               shape)
        return spiketrains_b_l_t

    def get_spiketrains_input(self):
        shape = list(self.parsed_model.input_shape) + [self._num_timesteps]
        spiketrains_flat = self.layers[0].get_data().segments[-1].spiketrains
        spiketrains_b_l_t = self.reshape_flattened_spiketrains(spiketrains_flat,
                                                               shape)
        return spiketrains_b_l_t

    def get_spiketrains_output(self):
        shape = [self.batch_size, self.num_classes, self._num_timesteps]
        spiketrains_flat = self.layers[-1].get_data().segments[-1].spiketrains
        spiketrains_b_l_t = self.reshape_flattened_spiketrains(spiketrains_flat,
                                                               shape)
        return spiketrains_b_l_t

    def get_vmem(self, **kwargs):
        vs = kwargs[str('layer')].get_data().segments[-1].analogsignals
        if len(vs) > 0:
            return np.array([np.swapaxes(v, 0, 1) for v in vs])

    def save_assembly(self, path, filename):
        """Write layers of neural network to disk.

        The size, structure, labels of all the population of an assembly are
        stored in a dictionary such that one can load them again using the
        `load_assembly` function.

        The term "assembly" refers to pyNN internal nomenclature, where
        ``Assembly`` is a collection of layers (``Populations``), which in turn
        consist of a number of neurons (``cells``).

        Parameters
        ----------

        path: str
            Path to directory where to save layers.

        filename: str
            Name of file to write layers to.
        """

        filepath = os.path.join(path, filename)

        if not (self.config.getboolean('output', 'overwrite') or
                confirm_overwrite(filepath)):
            return

        print("Saving assembly...")

        s = {}
        labels = []
        variables = ['size', 'structure', 'label']
        for population in self.layers:
            labels.append(population.label)
            data = {}
            for variable in variables:
                if hasattr(population, variable):
                    data[variable] = getattr(population, variable)
            if hasattr(population.celltype, 'describe'):
                data['celltype'] = population.celltype.describe()
            if population.label != 'InputLayer':
                data['i_offset'] = population.get('i_offset')
            s[population.label] = data
        s['labels'] = labels  # List of population labels describing the net.
        s['variables'] = variables  # List of variable names.
        s['size'] = len(self.layers)  # Number of populations in assembly.
        cPickle.dump(s, open(filepath, 'wb'), -1)

    def save_connections(self, path):
        """Write parameters of a neural network to disk.

        The parameters between two layers are saved in a text file.
        They can then be used to connect pyNN populations e.g. with
        ``sim.Projection(layer1, layer2, sim.FromListConnector(filename))``,
        where ``sim`` is a simulator supported by pyNN, e.g. Brian, NEURON, or
        NEST.

        Parameters
        ----------

        path: str
            Path to directory where connections are saved.

        Return
        ------

            Text files containing the layer connections. Each file is named
            after the layer it connects to, e.g. ``layer2.txt`` if connecting
            layer1 to layer2.
        """

        print("Saving connections...")

        # Iterate over layers to save each projection in a separate txt file.
        for projection in self.connections:
            filepath = os.path.join(path, projection.label.partition('→')[-1])
            if self.config.getboolean('output', 'overwrite') or \
                    confirm_overwrite(filepath):
                projection.save('connections', filepath)

    def save_biases(self, path):
        """Write biases of a neural network to disk.

        Parameters
        ----------

        path: str
            Path to directory where connections are saved.
        """

        print("Saving biases...")

        for layer in self.layers:
            filepath = os.path.join(path, layer.label + '_biases')
            if self.config.getboolean('output', 'overwrite') or \
                    confirm_overwrite(filepath):
                if 'Input' in layer.label:
                    continue
                try:
                    biases = layer.get('i_offset')
                except KeyError:
                    continue
                if np.isscalar(biases):
                    continue
                np.savetxt(filepath, biases)

    def load_assembly(self, path, filename):
        """Load the populations in an assembly.

        Loads the populations in an assembly that was saved with the
        `save_assembly` function.

        The term "assembly" refers to pyNN internal nomenclature, where
        ``Assembly`` is a collection of layers (``Populations``), which in turn
        consist of a number of neurons (``cells``).

        Parameters
        ----------

        path: str
            Path to directory where to load model from.

        filename: str
            Name of file to load model from.

        Returns
        -------

        layers: list[pyNN.Population]
            List of pyNN ``Population`` objects.
        """

        import sys

        filepath = os.path.join(path, filename)
        assert os.path.isfile(filepath), \
            "Spiking neuron layers were not found at specified location."
        if sys.version_info < (3,):
            s = cPickle.load(open(filepath, 'rb'))
        else:
            s = cPickle.load(open(filepath, 'rb'), encoding='bytes')

        # Iterate over populations in assembly
        layers = []
        for label in s['labels']:
            celltype = getattr(self.sim, s[label]['celltype'])
            population = self.sim.Population(s[label]['size'], celltype,
                                             celltype.default_parameters,
                                             structure=s[label]['structure'],
                                             label=label)
            # Set the rest of the specified variables, if any.
            for variable in s['variables']:
                if getattr(population, variable, None) is None:
                    setattr(population, variable, s[label][variable])
            if label != 'InputLayer':
                population.set(i_offset=s[label]['i_offset'])
            layers.append(population)

        return layers

    def set_spiketrain_stats_input(self):
        AbstractSNN.set_spiketrain_stats_input(self)


class MyProgressBar(object):
    """
    A callback which draws a progress bar in the terminal.
    """

    def __init__(self, interval, t_stop):
        self.interval = interval
        self.t_stop = t_stop
        self.pb = ProgressBar(width=int(t_stop / interval), char=".")

    def __call__(self, t):
        self.pb(t / self.t_stop)
        return t + self.interval


def get_shape_from_label(label):
    """
    Extract the output shape of a flattened pyNN layer from the layer name
    generated during parsing.

    Parameters
    ----------

    label: str
        Layer name containing shape information after a '_' separator.

    Returns
    -------

    : list
        The layer shape.

    Example
    -------
        >>> get_shape_from_label('02Conv2D_16x32x32')
        [16, 32, 32]

    """
    return [int(i) for i in label.split('_')[1].split('x')]


def get_snn_from_script(filepath):

    path = os.path.dirname(filepath)

    sys.path.append(os.path.abspath(path))

    filename, ext = os.path.splitext(os.path.basename(filepath))

    # Need to make sure that filename contains no dots to be able to import it.
    if '.' in filename:
        filename_new = filename.replace('.', '`')

        os.rename(os.path.join(path, filename + ext),
                  os.path.join(path, filename_new + ext))

        pynn_script = import_module(filename_new)

        os.rename(os.path.join(path, filename_new + ext),
                  os.path.join(path, filename + ext))
    else:
        pynn_script = import_module(filename)

    return pynn_script.get_model()
