from typing import List, Literal, Optional, Type, Union

import numpy as np
import supervision as sv
from pydantic import ConfigDict, Field, PositiveInt

from inference.core.entities.requests.sam2 import (
    Box,
    Sam2Prompt,
    Sam2PromptSet,
    Sam2SegmentationRequest,
)
from inference.core.entities.responses.inference import (
    InferenceResponseImage,
    InstanceSegmentationInferenceResponse,
    InstanceSegmentationPrediction,
    Point,
)
from inference.core.managers.base import ModelManager
from inference.core.utils.postprocess import masks2poly
from inference.core.workflows.core_steps.common.entities import StepExecutionMode
from inference.core.workflows.core_steps.common.utils import (
    attach_parents_coordinates_to_batch_of_sv_detections,
    attach_prediction_type_info_to_sv_detections_batch,
    convert_inference_detections_batch_to_sv_detections,
    load_core_model,
)
from inference.core.workflows.execution_engine.entities.base import (
    Batch,
    OutputDefinition,
    WorkflowImageData,
)
from inference.core.workflows.execution_engine.entities.types import (
    BATCH_OF_INSTANCE_SEGMENTATION_PREDICTION_KIND,
    BATCH_OF_KEYPOINT_DETECTION_PREDICTION_KIND,
    BATCH_OF_OBJECT_DETECTION_PREDICTION_KIND,
    STRING_KIND,
    ImageInputField,
    StepOutputImageSelector,
    StepOutputSelector,
    WorkflowImageSelector,
    WorkflowParameterSelector,
)
from inference.core.workflows.prototypes.block import (
    BlockResult,
    WorkflowBlock,
    WorkflowBlockManifest,
)

LONG_DESCRIPTION = """
Run Segment Anything 2 Model
"""


class BlockManifest(WorkflowBlockManifest):
    model_config = ConfigDict(
        json_schema_extra={
            "name": "Segment Anything 2 Model",
            "version": "v1",
            "short_description": "Segment Anything 2",
            "long_description": LONG_DESCRIPTION,
            "license": "Apache-2.0",
            "block_type": "model",
        },
        protected_namespaces=(),
    )
    type: Literal["roboflow_core/segment_anything@v1", "SegmentAnything2Model"]

    images: Union[WorkflowImageSelector, StepOutputImageSelector] = ImageInputField

    boxes: StepOutputSelector(
        kind=[
            BATCH_OF_OBJECT_DETECTION_PREDICTION_KIND,
            BATCH_OF_INSTANCE_SEGMENTATION_PREDICTION_KIND,
            BATCH_OF_KEYPOINT_DETECTION_PREDICTION_KIND,
        ]
    ) = Field(  # type: ignore
        description="Boxes (from other model predictions)",
        examples=["$steps.object_detection_model.predictions"],
        default=None,
    )

    sam2_model: Union[
        WorkflowParameterSelector(kind=[STRING_KIND]),
        Literal["hiera_large", "hiera_small", "hiera_tiny", "hiera_b_plus"],
    ] = Field(
        default="hiera_large",
        description="Model to be used.  One of hiera_large, hiera_small, hiera_tiny, hiera_b_plus",
        examples=["hiera_large", "$inputs.openai_model"],
    )

    @classmethod
    def accepts_batch_input(cls) -> bool:
        return True

    @classmethod
    def describe_outputs(cls) -> List[OutputDefinition]:
        return [
            OutputDefinition(
                name="predictions",
                kind=[BATCH_OF_INSTANCE_SEGMENTATION_PREDICTION_KIND],
            ),
        ]

    @classmethod
    def get_execution_engine_compatibility(cls) -> Optional[str]:
        return ">=1.0.0,<2.0.0"


class SegmentAnything2BlockV1(WorkflowBlock):

    def __init__(
        self,
        model_manager: ModelManager,
        api_key: Optional[str],
        step_execution_mode: StepExecutionMode,
    ):
        self._model_manager = model_manager
        self._api_key = api_key
        self._step_execution_mode = step_execution_mode

    @classmethod
    def get_init_parameters(cls) -> List[str]:
        return ["model_manager", "api_key", "step_execution_mode"]

    @classmethod
    def get_manifest(cls) -> Type[WorkflowBlockManifest]:
        return BlockManifest

    def run(
        self,
        images: Batch[WorkflowImageData],
        sam2_model: str,
        boxes: Batch[sv.Detections],
    ) -> BlockResult:
        if self._step_execution_mode is StepExecutionMode.LOCAL:
            return self.run_locally(images=images, sam2_model=sam2_model, boxes=boxes)
        elif self._step_execution_mode is StepExecutionMode.REMOTE:
            raise NotImplementedError(
                "Remote execution is not supported for Segment Anything."
            )
        else:
            raise ValueError(
                f"Unknown step execution mode: {self._step_execution_mode}"
            )

    def run_locally(
        self,
        images: Batch[WorkflowImageData],
        sam2_model: str,
        boxes: Batch[sv.Detections],
    ) -> BlockResult:

        predictions = []

        if not boxes:
            boxes = [None] * len(images)

        for single_image, boxes_for_image in zip(images, boxes):
            prompt_class_ids = []
            prompt_class_names = []
            prompt_index = 0

            prompts = Sam2PromptSet()
            if boxes_for_image is not None:
                for x1, y1, x2, y2 in boxes_for_image.xyxy:

                    prompt_class_ids.append(boxes_for_image.class_id[prompt_index])
                    prompt_class_names.append(
                        boxes_for_image.data["class_name"][prompt_index]
                    )
                    prompt_index += 1

                    width = x2 - x1
                    height = y2 - y1
                    cx = x1 + width / 2
                    cy = y1 + height / 2

                    prompt = Sam2Prompt(
                        box=Box(
                            x=cx,
                            y=cy,
                            width=width,
                            height=height,
                        )
                    )
                    prompts.add_prompt(prompt)

            inference_request = Sam2SegmentationRequest(
                image=single_image.to_inference_format(numpy_preferred=True),
                sam2_version_id=sam2_model,
                api_key=self._api_key,
                source="workflow-execution",
                prompts=prompts,
            )
            sam_model_id = load_core_model(
                model_manager=self._model_manager,
                inference_request=inference_request,
                core_model="sam2",
            )

            sam2_segmentation_response = self._model_manager.infer_from_request_sync(
                sam_model_id, inference_request
            )

            prediction = self._convert_sam2_segmentation_response_to_inference_instances_seg_response(
                sam2_segmentation_response.predictions,
                single_image,
                prompt_class_ids,
                prompt_class_names,
            )
            predictions.append(prediction)

        predictions = [
            e.model_dump(by_alias=True, exclude_none=True) for e in predictions
        ]
        return self._post_process_result(
            images=images,
            predictions=predictions,
        )

    def _convert_sam2_segmentation_response_to_inference_instances_seg_response(
        self, sam2_segmentation_predictions, image, prompt_class_ids, prompt_class_names
    ):
        image_width = image.numpy_image.shape[1]
        image_height = image.numpy_image.shape[0]
        predictions = []

        prediction_id = 0

        if len(prompt_class_ids) == 0:
            prompt_class_ids = [i for i in range(len(sam2_segmentation_predictions))]
            prompt_class_names = [
                str(i) for i in range(len(sam2_segmentation_predictions))
            ]

        for pred in sam2_segmentation_predictions:
            mask = pred.mask

            for polygon in mask:
                # for some reason this list of points contains empty array elements
                x_coords = [coord[0] for coord in polygon]
                y_coords = [coord[1] for coord in polygon]

                # Calculate min and max values
                min_x = np.min(x_coords)
                min_y = np.min(y_coords)
                max_x = np.max(x_coords)
                max_y = np.max(y_coords)

                # Calculate center coordinates
                center_x = (min_x + max_x) / 2
                center_y = (min_y + max_y) / 2

                predictions.append(
                    InstanceSegmentationPrediction(
                        **{
                            "x": center_x,
                            "y": center_y,
                            "width": max_x - min_x,
                            "height": max_y - min_y,
                            "points": [
                                Point(x=point[0], y=point[1]) for point in polygon
                            ],
                            "confidence": 0.5,  # TODO: get confidence from model
                            "class": prompt_class_names[prediction_id],
                            "class_id": prompt_class_ids[prediction_id],
                        }
                    )
                )
            prediction_id += 1

        return InstanceSegmentationInferenceResponse(
            predictions=predictions,
            image=InferenceResponseImage(width=image_width, height=image_height),
        )

    def _post_process_result(
        self,
        images: Batch[WorkflowImageData],
        predictions: List[dict],
    ) -> BlockResult:

        predictions = convert_inference_detections_batch_to_sv_detections(predictions)
        predictions = attach_prediction_type_info_to_sv_detections_batch(
            predictions=predictions,
            prediction_type="instance-segmentation",
        )
        predictions = attach_parents_coordinates_to_batch_of_sv_detections(
            images=images,
            predictions=predictions,
        )
        return [{"predictions": prediction} for prediction in predictions]