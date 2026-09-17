#!/usr/bin/env python3
"""Normalize Kokoro v1.1 ONNX for strict Intel GPU execution with OpenVINO."""
import argparse
import hashlib
from pathlib import Path

import onnx
from onnx import TensorProto, helper, numpy_helper


SOURCE_SHA256 = '859f9ded9f53be16c24857cdab3254a45da53c3afd5ba6ef134c7de3f822e326'
OPTIMIZATION_BARRIER = '/decoder/generator/Exp_output_0'


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def normalize(source, target):
    actual = sha256(source)
    if actual != SOURCE_SHA256:
        raise ValueError(f'Unexpected Kokoro source SHA256: {actual}')
    model = onnx.load(source)
    nodes = list(model.graph.node)

    squeeze_index = next(i for i, node in enumerate(nodes) if node.name == '/Squeeze')
    squeeze = nodes[squeeze_index]
    if list(squeeze.input) != ['/Cast_3_output_0']:
        raise ValueError('Unexpected duration Squeeze graph structure')
    squeeze.input.append('/ov/SqueezeDurationAxes_output_0')
    nodes.insert(squeeze_index, helper.make_node(
        'Constant', [], ['/ov/SqueezeDurationAxes_output_0'],
        name='/ov/SqueezeDurationAxes',
        value=helper.make_tensor('value', TensorProto.INT64, [1], [0]),
    ))

    first = next(i for i, node in enumerate(nodes) if node.name == '/SplitToSequence')
    last = next(i for i, node in enumerate(nodes) if node.name == '/ConcatFromSequence')
    sequence_ops = [node.op_type for node in nodes[first:last + 1]]
    expected_ops = ['SplitToSequence', 'SplitToSequence', 'Constant',
                    'SequenceEmpty', 'Loop', 'ConcatFromSequence']
    if sequence_ops != expected_ops:
        raise ValueError(f'Unexpected duration expansion graph: {sequence_ops}')
    replacement = [
        helper.make_node('CumSum', ['duration', '/Constant_18_output_0'],
                         ['/ov/CumSumDuration_output_0'], name='/ov/CumSumDuration'),
        helper.make_node('ReduceSum', ['duration', '/Constant_22_output_0'],
                         ['/ov/TotalDuration_output_0'], name='/ov/TotalDuration',
                         keepdims=0),
        helper.make_node('Range', ['/Constant_18_output_0',
                                   '/ov/TotalDuration_output_0',
                                   '/Constant_19_output_0'],
                         ['/ov/FrameRange_output_0'], name='/ov/FrameRange'),
        helper.make_node('Unsqueeze', ['/ov/FrameRange_output_0',
                                       '/Constant_24_output_0'],
                         ['/ov/FrameColumn_output_0'], name='/ov/FrameColumn'),
        helper.make_node('Unsqueeze', ['/ov/CumSumDuration_output_0',
                                       '/Constant_22_output_0'],
                         ['/ov/BoundaryRow_output_0'], name='/ov/BoundaryRow'),
        helper.make_node('Less', ['/ov/FrameColumn_output_0',
                                  '/ov/BoundaryRow_output_0'],
                         ['/ov/BeforeBoundary_output_0'], name='/ov/BeforeBoundary'),
        helper.make_node('Not', ['/ov/BeforeBoundary_output_0'],
                         ['/ov/AtOrPastBoundary_output_0'],
                         name='/ov/AtOrPastBoundary'),
        helper.make_node('Cast', ['/ov/AtOrPastBoundary_output_0'],
                         ['/ov/BoundaryCount_output_0'], name='/ov/BoundaryCount',
                         to=TensorProto.INT64),
        helper.make_node('ReduceSum', ['/ov/BoundaryCount_output_0',
                                       '/Constant_24_output_0'],
                         ['/ConcatFromSequence_output_0'], name='/ov/TokenForFrame',
                         keepdims=0),
    ]
    nodes = nodes[:first] + replacement + nodes[last + 1:]

    producers = {output: node for node in nodes for output in node.output}
    expanded = []
    resize_count = 0
    for node in nodes:
        if node.op_type != 'Resize':
            expanded.append(node)
            continue
        resize_count += 1
        scale_node = producers[node.input[2]]
        scale = numpy_helper.to_array(next(
            attribute.t for attribute in scale_node.attribute
            if attribute.name == 'value'
        )).tolist()
        if len(scale) != 3 or scale[:2] != [1.0, 1.0]:
            raise ValueError(f'Unexpected Resize scale for {node.name}: {scale}')
        prefix = f'{node.name}/ov_rank2'
        original_input, original_output = node.input[0], node.output[0]
        axes_output = f'{prefix}/axes_output_0'
        scales_output = f'{prefix}/scales_output_0'
        input_output = f'{prefix}/input_output_0'
        resize_output = f'{prefix}/resize_output_0'
        expanded.extend([
            helper.make_node(
                'Constant', [], [axes_output], name=f'{prefix}/axes',
                value=helper.make_tensor('value', TensorProto.INT64, [1], [0]),
            ),
            helper.make_node(
                'Constant', [], [scales_output], name=f'{prefix}/scales',
                value=helper.make_tensor('value', TensorProto.FLOAT, [2],
                                         [scale[1], scale[2]]),
            ),
            helper.make_node('Squeeze', [original_input, axes_output], [input_output],
                             name=f'{prefix}/squeeze_batch'),
        ])
        node.input[0], node.input[2], node.output[0] = (
            input_output, scales_output, resize_output,
        )
        expanded.extend([
            node,
            helper.make_node('Unsqueeze', [resize_output, axes_output],
                             [original_output], name=f'{prefix}/unsqueeze_batch'),
        ])
    if resize_count != 6:
        raise ValueError(f'Expected six temporal Resize nodes, got {resize_count}')

    model.graph.ClearField('node')
    model.graph.node.extend(expanded)
    model.graph.output.append(helper.make_tensor_value_info(
        OPTIMIZATION_BARRIER, TensorProto.FLOAT, [1, 11, 'phase_frames']
    ))

    needed = {value.name for value in model.graph.output}
    kept = []
    for node in reversed(model.graph.node):
        if any(output in needed for output in node.output):
            kept.append(node)
            needed.update(value for value in node.input if value)
    kept.reverse()
    model.graph.ClearField('node')
    model.graph.node.extend(kept)
    onnx.checker.check_model(model, full_check=True)
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    onnx.save(model, target)
    return sha256(target)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(normalize(args.source, args.output))


if __name__ == '__main__':
    main()
