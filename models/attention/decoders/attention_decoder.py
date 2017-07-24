#! /usr/bin/env python
# -*- coding: utf-8 -*-

"""A basic sequence decoder that performs a softmax based on the RNN state."""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

from collections import namedtuple
import tensorflow as tf
from tensorflow.python.util import nest

from models.attention.decoders.dynamic_decoder import dynamic_decode


class AttentionDecoderOutput(namedtuple(
        "DecoderOutput",
        [
            "logits",
            "predicted_ids",
            "cell_output",
            "attention_weights",
            "attention_context"
        ])):
    """
    Args:
        logits:
        predicted_ids:
        cell_output:
        attention_weights:
        attention_context:
    """
    pass


class RNNDecoder(tf.contrib.seq2seq.Decoder):

    def __init__(self,
                 cell,
                 parameter_init,
                 max_decode_length,
                 num_classes,
                 attention_encoder_states,
                 attention_values,
                 attention_values_length,
                 attention_layer,
                 time_major,
                 name=None):
        self.cell = cell
        self.parameter_init = parameter_init
        self.max_decode_length = max_decode_length
        self.num_classes = num_classes
        self.attention_encoder_states = attention_encoder_states
        self.attention_values = attention_values
        self.attention_values_length = attention_values_length
        self.attention_layer = attention_layer  # AttentionLayer class
        self.time_major = time_major
        self.name = name

        # Not initialized yet
        self.initial_state = None
        self.helper = None

    def _setup(self, initial_state, helper):
        """Sets the initial state and helper for the decoder."""
        print('===== RNNDecoder setup =====')
        self.initial_state = initial_state
        self.helper = helper

    def _build(self):
        raise NotImplementedError


class AttentionDecoder(RNNDecoder):
    """An RNN Decoder that uses attention over an input sequence.
    Args:
        cell: An instance of ` tf.contrib.rnn.RNNCell` (LSTM, GRU is also OK)
        parameter_init: A float value. Range of uniform distribution to
            initialize weight parameters
        max_decode_length: int, the length of output sequences to stop
            prediction when EOS token have not been emitted
        num_classes: Output vocabulary size,
             i.e. number of units in the softmax layer
        attention_encoder_states: The sequence used to calculate attention
            scores. A tensor of shape
            `[batch_size, input_time, encoder_num_unit]`.
        attention_values: The sequence to attend over.
            A tensor of shape `[batch_size, input_time, encoder_num_unit]`.
        attention_values_length: Sequence length of the attention values.
            An int32 Tensor of shape `[batch_size]`.
        attention_layer: The attention function to use. This function map from
            `(state, inputs)` to `(attention_weights, attention_context)`.
            For an example, see `decoders.attention_layer.AttentionLayer`.
        time-major: bool,
    """

    def __init__(self,
                 cell,
                 parameter_init,
                 max_decode_length,
                 num_classes,
                 attention_encoder_states,
                 attention_values,
                 attention_values_length,
                 attention_layer,
                 time_major,
                 name='attention_decoder'):
        super(AttentionDecoder, self).__init__(cell,
                                               parameter_init,
                                               max_decode_length,
                                               num_classes,
                                               attention_encoder_states,
                                               attention_values,
                                               attention_values_length,
                                               attention_layer,
                                               time_major,
                                               name)

        self.reuse = True
        # NOTE: This is for beam search decoder
        # When training mode, this will be overwritten in self._build()

    def __call__(self, *args, **kwargs):
        with tf.variable_scope(self.name):
            return self._build(*args, **kwargs)

    @property
    def output_size(self):
        return AttentionDecoderOutput(
            logits=self.num_classes,
            predicted_ids=tf.TensorShape([]),
            cell_output=self.cell.output_size,
            attention_weights=tf.shape(self.attention_values)[1:-1],
            attention_context=self.attention_values.get_shape()[-1])

    @property
    def output_dtype(self):
        return AttentionDecoderOutput(
            logits=tf.float32,
            predicted_ids=tf.int32,
            cell_output=tf.float32,
            attention_weights=tf.float32,
            attention_context=tf.float32)

    @property
    def batch_size(self):
        return tf.shape(nest.flatten([self.initial_state])[0])[0]

    def _build(self, initial_state, helper, mode):
        """
        Args:
            initial_state: A tensor or tuple of tensors used as the initial
                cell state. Set to the final state of the encoder by default.
            helper: An instance of `tf.contrib.seq2seq.Helper` to assist
                decoding
            mode:
        Returns:
            A tuple of `(outputs, final_state)`
                outputs: A tensor of `[time, batch_size, ??]`
                final_state: A tensor of `[time, batch_size, ??]`
        """
        print('===== AttentionDecoder build =====')
        self.mode = mode

        # Initialize
        if not self.initial_state:
            self._setup(initial_state, helper)
        # NOTE: ignore if wrap attention_decoder by beam_search_decoder

        scope = tf.get_variable_scope()
        scope.set_initializer(tf.random_uniform_initializer(
            -self.parameter_init,
            self.parameter_init))

        if mode == tf.contrib.learn.ModeKeys.TRAIN:
            self.reuse = False
            maximum_iterations = None
        else:
            self.reuse = True
            maximum_iterations = self.max_decode_length

        # outputs, final_state, final_seq_len =
        # tf.contrib.seq2seq.dynamic_decode(
        outputs, final_state = dynamic_decode(
            decoder=self,
            output_time_major=self.time_major,
            impute_finished=True,
            maximum_iterations=maximum_iterations,
            scope='dynamic_decoder')

        # tf.contrib.seq2seq.dynamic_decode
        # return self.finalize(outputs, final_state, final_seq_len)

        # ./dynamic_decoder.py
        return self.finalize(outputs, final_state, None)

    def finalize(self, outputs, final_state, final_seq_len):
        """Applies final transformation to the decoder output once decoding is
           finished.
        Args:
            outputs:
            final_state:
            final_seq_len:
        Returns:
            outputs:
            final_state:
        """
        print('===== finalize =====')
        return outputs, final_state

    def initialize(self, name=None):
        """Initialize the decoder.
        Args:
            name: Name scope for any created operations
        Returns:
            finished:
            first_inputs:
            initial_state:
        """
        print('=== initialize =====')
        # Create inputs for the first time step
        finished, first_inputs = self.helper.initialize()
        # NOTE: first_inputs: `[batch_size, embedding_dim]`

        # Concat empty attention context
        batch_size = tf.shape(first_inputs)[0]
        encoder_num_unit = self.attention_values.get_shape().as_list()[-1]
        attention_context = tf.zeros(shape=[batch_size, encoder_num_unit])
        self.attention_weights = tf.zeros(
            shape=[batch_size, tf.shape(self.attention_values)[1]])

        # Create first inputs
        first_inputs = tf.concat([first_inputs, attention_context], axis=1)
        # ex.) tf.concat
        # tensor t3 with shape [2, 3]
        # tensor t4 with shape [2, 3]
        # tf.shape(tf.concat([t3, t4], 0)) ==> [4, 3]
        # tf.shape(tf.concat([t3, t4], 1)) ==> [2, 6]

        return finished, first_inputs, self.initial_state

    def compute_output(self, cell_output, attention_weights):
        """Computes the decoder outputs at each time.
        Args:
            cell_output: The previous state of the decoder
            attention_weights:
        Returns:
            softmax_input: A tensor of size `[]`
            logits: A tensor of size `[]`
            attention_weights: A tensor of size `[]`
            attention_context: A tensor of szie `[]`
        """
        print('===== compute_output =====')
        # Compute attention weights & context
        attention_weights, attention_context = self.attention_layer(
            encoder_states=self.attention_encoder_states,
            current_decoder_state=cell_output,
            values=self.attention_values,
            values_length=self.attention_values_length,
            attention_weights=attention_weights)

        # TODO: Make this a parameter: We may or may not want this.
        # Transform attention context.
        # This makes the softmax smaller and allows us to synthesize
        # information between decoder state and attention context
        # see https://arxiv.org/abs/1508.04025v5
        # g_i = tanh(W_s * s_{i-1} + W_c * c_i + b (+ W_o * y_{i-1}))
        # TODO: y_i-1も入力にするのは冗長らしいが，自分で確かめる
        self.softmax_input = tf.contrib.layers.fully_connected(
            inputs=tf.concat([cell_output, attention_context], axis=1),
            num_outputs=self.cell.output_size,
            activation_fn=tf.nn.tanh,
            # reuse=True,
            scope="attention_mix")

        # Softmax computation
        # P(y_i|s_i, c_i, y_{i-1}) = softmax(W_g * g_i + b)
        logits = tf.contrib.layers.fully_connected(
            inputs=self.softmax_input,
            num_outputs=self.num_classes,
            activation_fn=None,
            # reuse=True,
            scope="logits")
        self.logits = logits

        return (self.softmax_input, logits,
                attention_weights, attention_context)

    def _setup(self, initial_state, helper):
        """Define original helper function."""
        print('===== attention decoder setup =====')
        self.initial_state = initial_state
        self.helper = helper

        def att_next_inputs(time, outputs, state, sample_ids, name=None):
            """Wraps the original decoder helper function to append the
               attention context.
            Args:
                time:
                outputs:
                state:
                sample_ids:
                name:
            Returs:
                A tuple of `(finished, next_inputs, next_state)`
            """
            finished, next_inputs, next_state = helper.next_inputs(
                time=time,
                outputs=outputs,
                state=state,
                sample_ids=sample_ids,
                name=name)

            next_inputs = tf.concat(
                [next_inputs, outputs.attention_context], axis=1)

            return finished, next_inputs, next_state

        self.helper = tf.contrib.seq2seq.CustomHelper(
            initialize_fn=helper.initialize,
            sample_fn=helper.sample,
            next_inputs_fn=att_next_inputs)

    def step(self, time, inputs, state, name=None):
        """Perform a decoding step.
        Args:
           time: scalar `int32` tensor
           inputs: A input tensors
           state: A state tensors and TensorArrays
           name: Name scope for any created operations
        Returns:
            outputs: An instance of AttentionDecoderOutput
            next_state: A state tensors and TensorArrays
            next_inputs: The tensor that should be used as input for the
                next step
            finished: A boolean tensor telling whether the sequence is
                complete, for each sequence in the batch
        """
        print('===== step =====')
        with tf.variable_scope("step", reuse=self.reuse):
            # Call LSTMCell
            cell_output_prev, cell_state_prev = self.cell(inputs, state)
            attention_weights_prev = self.attention_weights
            cell_output, logits, attention_weights, attention_context = self.compute_output(
                cell_output_prev, attention_weights_prev)
            self.attention_weights = attention_weights

            sample_ids = self.helper.sample(time=time,
                                            outputs=logits,
                                            state=cell_state_prev)
            # TODO: Trainingのときlogitsの値はone-hotまたは一意のベクトルに変換されているか？

            outputs = AttentionDecoderOutput(logits=logits,
                                             predicted_ids=sample_ids,
                                             cell_output=cell_output,
                                             attention_weights=attention_weights,
                                             attention_context=attention_context)

            finished, next_inputs, next_state = self.helper.next_inputs(
                time=time,
                outputs=outputs,
                state=cell_state_prev,
                sample_ids=sample_ids)

            return outputs, next_state, next_inputs, finished
