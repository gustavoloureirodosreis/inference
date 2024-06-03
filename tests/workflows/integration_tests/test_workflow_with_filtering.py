import numpy as np
import pytest
import supervision as sv

from inference.core.env import WORKFLOWS_MAX_CONCURRENT_STEPS
from inference.core.managers.base import ModelManager
from inference.core.workflows.core_steps.common.query_language.errors import (
    EvaluationEngineError,
)
from inference.core.workflows.entities.base import StepExecutionMode
from inference.core.workflows.errors import RuntimeInputError, StepExecutionError
from inference.core.workflows.execution_engine.core import ExecutionEngine

FILTERING_WORKFLOW = {
    "version": "1.0",
    "inputs": [
        {"type": "WorkflowImage", "name": "image"},
        {"type": "WorkflowParameter", "name": "model_id"},
        {"type": "WorkflowParameter", "name": "confidence", "default_value": 0.3},
        {"type": "WorkflowParameter", "name": "classes"},
    ],
    "steps": [
        {
            "type": "RoboflowObjectDetectionModel",
            "name": "detection",
            "image": "$inputs.image",
            "model_id": "$inputs.model_id",
            "confidence": "$inputs.confidence",
        },
        {
            "type": "DetectionsTransformation",
            "name": "filtering",
            "predictions": "$steps.detection.predictions",
            "operations": [
                {
                    "type": "DetectionsFilter",
                    "filter_operation": {
                        "type": "StatementGroup",
                        "operator": "and",
                        "statements": [
                            {
                                "type": "BinaryStatement",
                                "left_operand": {
                                    "type": "DynamicOperand",
                                    "operations": [
                                        {
                                            "type": "ExtractDetectionProperty",
                                            "property_name": "class_name",
                                        }
                                    ],
                                },
                                "comparator": {"type": "in (Sequence)"},
                                "right_operand": {
                                    "type": "DynamicOperand",
                                    "operand_name": "classes",
                                },
                            },
                            {
                                "type": "BinaryStatement",
                                "left_operand": {
                                    "type": "DynamicOperand",
                                    "operations": [
                                        {
                                            "type": "ExtractDetectionProperty",
                                            "property_name": "size",
                                        },
                                    ],
                                },
                                "comparator": {"type": "(Number) >="},
                                "right_operand": {
                                    "type": "DynamicOperand",
                                    "operand_name": "image",
                                    "operations": [
                                        {
                                            "type": "ExtractImageProperty",
                                            "property_name": "size",
                                        },
                                        {"type": "Multiply", "other": 0.02},
                                    ],
                                },
                            },
                        ],
                    },
                }
            ],
            "operations_parameters": {
                "image": "$inputs.image",
                "classes": "$inputs.classes",
            },
        },
    ],
    "outputs": [
        {"type": "JsonField", "name": "result", "selector": "$steps.filtering.*"}
    ],
}

EXPECTED_OBJECT_DETECTION_BBOXES = np.array(
    [
        [180, 273, 244, 383],
        [271, 266, 328, 383],
    ]
)
EXPECTED_OBJECT_DETECTION_CONFIDENCES = np.array(
    [
        0.84284,
        0.83957,
    ]
)


@pytest.mark.asyncio
async def test_filtering_workflow_when_minimal_valid_input_provided(
    model_manager: ModelManager,
    crowd_image: np.ndarray,
) -> None:
    # given
    workflow_init_parameters = {
        "workflows_core.model_manager": model_manager,
        "workflows_core.api_key": None,
    }
    execution_engine = ExecutionEngine.init(
        workflow_definition=FILTERING_WORKFLOW,
        init_parameters=workflow_init_parameters,
        max_concurrent_steps=WORKFLOWS_MAX_CONCURRENT_STEPS,
        step_execution_mode=StepExecutionMode.LOCAL,
    )

    # when
    result = await execution_engine.run_async(
        runtime_parameters={
            "image": crowd_image,
            "model_id": "yolov8n-640",
            "classes": {"person"},
        }
    )

    # then
    assert set(result.keys()) == {
        "result"
    }, "Only single output key should be extracted"
    assert len(result["result"]) == 1, "Result for single image is expected"
    detections: sv.Detections = result["result"][0]["predictions"]
    assert np.allclose(
        detections.xyxy,
        EXPECTED_OBJECT_DETECTION_BBOXES,
        atol=1,
    ), "Expected bboxes to match what was validated manually as workflow outcome"
    assert np.allclose(
        detections.confidence,
        EXPECTED_OBJECT_DETECTION_CONFIDENCES,
        atol=0.01,
    ), "Expected confidences to match what was validated manually as workflow outcome"


@pytest.mark.asyncio
async def test_filtering_workflow_when_batch_input_provided(
    model_manager: ModelManager,
    crowd_image: np.ndarray,
) -> None:
    # given
    workflow_init_parameters = {
        "workflows_core.model_manager": model_manager,
        "workflows_core.api_key": None,
    }
    execution_engine = ExecutionEngine.init(
        workflow_definition=FILTERING_WORKFLOW,
        init_parameters=workflow_init_parameters,
        max_concurrent_steps=WORKFLOWS_MAX_CONCURRENT_STEPS,
        step_execution_mode=StepExecutionMode.LOCAL,
    )

    # when
    result = await execution_engine.run_async(
        runtime_parameters={
            "image": [crowd_image, crowd_image],
            "model_id": "yolov8n-640",
            "classes": {"person"},
        }
    )

    # then
    assert set(result.keys()) == {
        "result"
    }, "Only single output key should be extracted"
    assert len(result["result"]) == 2, "Results for botch images are expected"
    detections_1: sv.Detections = result["result"][0]["predictions"]
    detections_2: sv.Detections = result["result"][1]["predictions"]
    assert np.allclose(
        detections_1.xyxy,
        EXPECTED_OBJECT_DETECTION_BBOXES,
        atol=1,
    ), "Expected bboxes for first image to match what was validated manually as workflow outcome"
    assert np.allclose(
        detections_1.confidence,
        EXPECTED_OBJECT_DETECTION_CONFIDENCES,
        atol=0.01,
    ), "Expected confidences for first image to match what was validated manually as workflow outcome"
    assert np.allclose(
        detections_2.xyxy,
        EXPECTED_OBJECT_DETECTION_BBOXES,
        atol=1,
    ), "Expected bboxes for 2nd image to match what was validated manually as workflow outcome"
    assert np.allclose(
        detections_2.confidence,
        EXPECTED_OBJECT_DETECTION_CONFIDENCES,
        atol=0.01,
    ), "Expected confidences for 2nd image to match what was validated manually as workflow outcome"


@pytest.mark.asyncio
async def test_filtering_workflow_when_model_id_not_provided_in_input(
    model_manager: ModelManager,
    crowd_image: np.ndarray,
) -> None:
    # given
    workflow_init_parameters = {
        "workflows_core.model_manager": model_manager,
        "workflows_core.api_key": None,
    }
    execution_engine = ExecutionEngine.init(
        workflow_definition=FILTERING_WORKFLOW,
        init_parameters=workflow_init_parameters,
        max_concurrent_steps=WORKFLOWS_MAX_CONCURRENT_STEPS,
        step_execution_mode=StepExecutionMode.LOCAL,
    )

    # when
    with pytest.raises(RuntimeInputError):
        _ = await execution_engine.run_async(
            runtime_parameters={
                "image": crowd_image,
            }
        )


@pytest.mark.asyncio
async def test_filtering_workflow_when_image_not_provided_in_input(
    model_manager: ModelManager,
) -> None:
    # given
    workflow_init_parameters = {
        "workflows_core.model_manager": model_manager,
        "workflows_core.api_key": None,
        "classes": {"person"},
    }
    execution_engine = ExecutionEngine.init(
        workflow_definition=FILTERING_WORKFLOW,
        init_parameters=workflow_init_parameters,
        max_concurrent_steps=WORKFLOWS_MAX_CONCURRENT_STEPS,
        step_execution_mode=StepExecutionMode.LOCAL,
    )

    # when
    with pytest.raises(RuntimeInputError):
        _ = await execution_engine.run_async(
            runtime_parameters={
                "model_id": "yolov8n-640",
            }
        )


@pytest.mark.asyncio
async def test_filtering_workflow_when_classes_not_provided(
    model_manager: ModelManager,
    crowd_image: np.ndarray,
) -> None:
    # given
    workflow_init_parameters = {
        "workflows_core.model_manager": model_manager,
        "workflows_core.api_key": None,
    }
    execution_engine = ExecutionEngine.init(
        workflow_definition=FILTERING_WORKFLOW,
        init_parameters=workflow_init_parameters,
        max_concurrent_steps=WORKFLOWS_MAX_CONCURRENT_STEPS,
        step_execution_mode=StepExecutionMode.LOCAL,
    )

    # when
    with pytest.raises(EvaluationEngineError):
        _ = await execution_engine.run_async(
            runtime_parameters={
                "image": crowd_image,
                "model_id": "yolov8n-640",
            }
        )


@pytest.mark.asyncio
async def test_filtering_workflow_when_model_id_cannot_be_resolved_to_valid_model(
    model_manager: ModelManager,
    crowd_image: np.ndarray,
) -> None:
    # given
    workflow_init_parameters = {
        "workflows_core.model_manager": model_manager,
        "workflows_core.api_key": None,
    }
    execution_engine = ExecutionEngine.init(
        workflow_definition=FILTERING_WORKFLOW,
        init_parameters=workflow_init_parameters,
        max_concurrent_steps=WORKFLOWS_MAX_CONCURRENT_STEPS,
        step_execution_mode=StepExecutionMode.LOCAL,
    )

    # when
    with pytest.raises(StepExecutionError):
        _ = await execution_engine.run_async(
            runtime_parameters={
                "image": crowd_image,
                "model_id": "invalid",
            }
        )