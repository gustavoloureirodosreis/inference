import numpy as np
import pytest
import supervision as sv

from inference.core.env import WORKFLOWS_MAX_CONCURRENT_STEPS
from inference.core.managers.base import ModelManager
from inference.core.workflows.entities.base import StepExecutionMode
from inference.core.workflows.errors import RuntimeInputError, StepExecutionError
from inference.core.workflows.execution_engine.core import ExecutionEngine

CONSENSUS_WORKFLOW = {
    "version": "1.0",
    "inputs": [
        {"type": "WorkflowImage", "name": "image"},
        {"type": "WorkflowParameter", "name": "model_id"},
    ],
    "steps": [
        {
            "type": "RoboflowObjectDetectionModel",
            "name": "detection_1",
            "image": "$inputs.image",
            "model_id": "$inputs.model_id",
            "confidence": 0.3,
        },
        {
            "type": "RoboflowObjectDetectionModel",
            "name": "detection_2",
            "image": "$inputs.image",
            "model_id": "$inputs.model_id",
            "confidence": 0.83,
        },
        {
            "type": "DetectionsConsensus",
            "name": "consensus",
            "predictions_batches": [
                "$steps.detection_1.predictions",
                "$steps.detection_2.predictions",
            ],
            "required_votes": 2,
            "required_objects": {
                "person": 2,
            },
        },
    ],
    "outputs": [
        {"type": "JsonField", "name": "result", "selector": "$steps.consensus.*"}
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
async def test_consensus_workflow_when_minimal_valid_input_provided(
    model_manager: ModelManager,
    crowd_image: np.ndarray,
) -> None:
    # given
    workflow_init_parameters = {
        "workflows_core.model_manager": model_manager,
        "workflows_core.api_key": None,
    }
    execution_engine = ExecutionEngine.init(
        workflow_definition=CONSENSUS_WORKFLOW,
        init_parameters=workflow_init_parameters,
        max_concurrent_steps=WORKFLOWS_MAX_CONCURRENT_STEPS,
        step_execution_mode=StepExecutionMode.LOCAL,
    )

    # when
    result = await execution_engine.run_async(
        runtime_parameters={"image": crowd_image, "model_id": "yolov8n-640"}
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
    assert (
        result["result"][0]["object_present"] is True
    ), "Detected 2 instances of person in combined prediction, so `object_present` should be marked True"
    assert (
        abs(result["result"][0]["presence_confidence"]["person"] - 0.84284) < 1e-4
    ), "Expected presence confidence to be max of merged person class confidence"


@pytest.mark.asyncio
async def test_consensus_workflow_when_batch_input_provided(
    model_manager: ModelManager,
    crowd_image: np.ndarray,
) -> None:
    # given
    workflow_init_parameters = {
        "workflows_core.model_manager": model_manager,
        "workflows_core.api_key": None,
    }
    execution_engine = ExecutionEngine.init(
        workflow_definition=CONSENSUS_WORKFLOW,
        init_parameters=workflow_init_parameters,
        max_concurrent_steps=WORKFLOWS_MAX_CONCURRENT_STEPS,
        step_execution_mode=StepExecutionMode.LOCAL,
    )

    # when
    result = await execution_engine.run_async(
        runtime_parameters={
            "image": [crowd_image, crowd_image],
            "model_id": "yolov8n-640",
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
async def test_consensus_workflow_when_confidence_is_restricted_by_input_parameter(
    model_manager: ModelManager,
    crowd_image: np.ndarray,
) -> None:
    # given
    workflow_init_parameters = {
        "workflows_core.model_manager": model_manager,
        "workflows_core.api_key": None,
    }
    execution_engine = ExecutionEngine.init(
        workflow_definition=CONSENSUS_WORKFLOW,
        init_parameters=workflow_init_parameters,
        max_concurrent_steps=WORKFLOWS_MAX_CONCURRENT_STEPS,
        step_execution_mode=StepExecutionMode.LOCAL,
    )

    # when
    result = await execution_engine.run_async(
        runtime_parameters={
            "image": crowd_image,
            "model_id": "yolov8n-640",
            "confidence": 0.8,
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
        EXPECTED_OBJECT_DETECTION_BBOXES[:4],
        atol=1,
    ), "Expected bboxes to match what was validated manually as workflow outcome"
    assert np.allclose(
        detections.confidence,
        EXPECTED_OBJECT_DETECTION_CONFIDENCES[:4],
        atol=0.01,
    ), "Expected confidences to match what was validated manually as workflow outcome"


@pytest.mark.asyncio
async def test_consensus_workflow_when_model_id_not_provided_in_input(
    model_manager: ModelManager,
    crowd_image: np.ndarray,
) -> None:
    # given
    workflow_init_parameters = {
        "workflows_core.model_manager": model_manager,
        "workflows_core.api_key": None,
    }
    execution_engine = ExecutionEngine.init(
        workflow_definition=CONSENSUS_WORKFLOW,
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
async def test_consensus_workflow_when_image_not_provided_in_input(
    model_manager: ModelManager,
) -> None:
    # given
    workflow_init_parameters = {
        "workflows_core.model_manager": model_manager,
        "workflows_core.api_key": None,
    }
    execution_engine = ExecutionEngine.init(
        workflow_definition=CONSENSUS_WORKFLOW,
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
async def test_consensus_workflow_when_model_id_cannot_be_resolved_to_valid_model(
    model_manager: ModelManager,
    crowd_image: np.ndarray,
) -> None:
    # given
    workflow_init_parameters = {
        "workflows_core.model_manager": model_manager,
        "workflows_core.api_key": None,
    }
    execution_engine = ExecutionEngine.init(
        workflow_definition=CONSENSUS_WORKFLOW,
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