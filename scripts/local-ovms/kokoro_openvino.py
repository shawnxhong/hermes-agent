"""Strict OpenVINO GPU session adapter for ``kokoro-onnx``.

The adapter intentionally exposes only the two public Kokoro outputs while the
normalized model retains a third output as an OpenVINO GPU optimization
boundary. Compilation targets GPU directly; AUTO, HETERO and CPU fallback are
not accepted.
"""
from pathlib import Path
from types import SimpleNamespace

import numpy as np


_ORT_TYPES = {
    'i64': 'tensor(int64)',
    'i32': 'tensor(int32)',
    'f32': 'tensor(float)',
    'f16': 'tensor(float16)',
    'f64': 'tensor(double)',
}
_PUBLIC_OUTPUTS = ('waveform', 'duration')
_OPTIMIZATION_BARRIER = '/decoder/generator/Exp_output_0'


class OpenVINOSession:
    """Present a small ONNX Runtime-compatible interface backed by OpenVINO."""

    def __init__(self, model_path: Path, cache_dir: Path):
        import openvino as ov

        self._model_path = str(model_path)
        core = ov.Core()
        if 'GPU' not in core.available_devices:
            raise RuntimeError(f'OpenVINO GPU unavailable: {core.available_devices}')
        model = core.read_model(self._model_path)
        model_outputs = tuple(value.get_any_name() for value in model.outputs)
        expected = _PUBLIC_OUTPUTS + (_OPTIMIZATION_BARRIER,)
        if model_outputs != expected:
            raise RuntimeError(
                f'Normalized Kokoro outputs must be {expected}, got {model_outputs}'
            )
        self.compiled = core.compile_model(model, 'GPU', {
            'PERFORMANCE_HINT': 'LATENCY',
            'EXECUTION_MODE_HINT': 'ACCURACY',
            'INFERENCE_PRECISION_HINT': 'f32',
            'CACHE_DIR': str(cache_dir),
        })
        devices = tuple(str(value) for value in
                        self.compiled.get_property('EXECUTION_DEVICES'))
        if not devices or any(not value.startswith('GPU') for value in devices):
            raise RuntimeError(f'Refusing non-GPU Kokoro execution devices: {devices}')
        self.execution_devices = devices
        self.device_name = ','.join(devices)
        self._inputs = [SimpleNamespace(
            name=value.get_any_name(),
            type=_ORT_TYPES[value.get_element_type().get_type_name()],
        ) for value in self.compiled.inputs]
        outputs = {value.get_any_name(): value for value in self.compiled.outputs}
        self._outputs = [SimpleNamespace(name=name) for name in _PUBLIC_OUTPUTS]
        self._output_ports = {name: outputs[name] for name in _PUBLIC_OUTPUTS}

    def get_inputs(self):
        return self._inputs

    def get_outputs(self):
        return self._outputs

    def run(self, output_names, inputs):
        names = tuple(output_names) if output_names else _PUBLIC_OUTPUTS
        unknown = set(names).difference(_PUBLIC_OUTPUTS)
        if unknown:
            raise ValueError(f'Unknown Kokoro outputs requested: {sorted(unknown)}')
        result = self.compiled(inputs)
        return [np.array(result[self._output_ports[name]], copy=True) for name in names]
