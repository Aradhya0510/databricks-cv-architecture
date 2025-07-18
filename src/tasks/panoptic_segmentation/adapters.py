from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, List, Tuple
import torch
from transformers import AutoImageProcessor
from PIL import Image
import torchvision.transforms.functional as F

class BaseAdapter(ABC):
    """Base class for model-specific data adapters."""
    
    @abstractmethod
    def __call__(self, image: Image.Image, target: Dict) -> Tuple[torch.Tensor, Dict]:
        """Process a single sample.
        
        Args:
            image: A PIL image
            target: A dictionary containing segmentation masks and metadata
            
        Returns:
            A tuple containing the processed image tensor and target dictionary
        """
        pass

class NoOpInputAdapter(BaseAdapter):
    """A "No-Operation" input adapter.
    - Converts image to a tensor
    - Keeps targets in standard format
    - Suitable for models like torchvision's segmentation models
    """
    def __call__(self, image: Image.Image, target: Dict) -> Tuple[torch.Tensor, Dict]:
        return F.to_tensor(image), target

class Mask2FormerInputAdapter(BaseAdapter):
    """Input adapter for Mask2Former models.
    - Handles image resizing and preprocessing for Mask2Former architecture
    - Optimized for panoptic segmentation
    """
    def __init__(self, model_name: str, image_size: int = 512):
        self.image_size = image_size
        self.processor = AutoImageProcessor.from_pretrained(
            model_name,
            size={"height": image_size, "width": image_size},
            do_resize=True,
            do_rescale=True,
            do_normalize=True,
            do_pad=True
        )
    
    def __call__(self, image: Image.Image, target: Dict) -> Tuple[torch.Tensor, Dict]:
        # Use processor for complete image preprocessing
        processed = self.processor(
            image,
            return_tensors="pt",
            do_resize=True,
            do_rescale=True,
            do_normalize=True,
            do_pad=True
        )
        
        return processed.pixel_values.squeeze(0), target

class OutputAdapter(ABC):
    """Base class for model output adapters."""
    
    @abstractmethod
    def adapt_output(self, outputs: Dict[str, Any]) -> Dict[str, Any]:
        """Adapt model outputs to standard format.
        
        Args:
            outputs: Raw model outputs
            
        Returns:
            Adapted outputs in standard format
        """
        pass
    
    @abstractmethod
    def adapt_targets(self, targets: Dict[str, torch.Tensor]) -> Dict[str, torch.Tensor]:
        """Adapt targets to model-specific format.
        
        Args:
            targets: Standard format targets
            
        Returns:
            Adapted targets in model-specific format
        """
        pass
    
    @abstractmethod
    def format_predictions(self, outputs: Dict[str, Any]) -> List[Dict[str, torch.Tensor]]:
        """Format model outputs for metric computation.
        
        Args:
            outputs: Model outputs dictionary
            
        Returns:
            List of prediction dictionaries for each image
        """
        pass
    
    @abstractmethod
    def format_targets(self, targets: Dict[str, torch.Tensor]) -> List[Dict[str, torch.Tensor]]:
        """Format targets for metric computation.
        
        Args:
            targets: Standard format targets
            
        Returns:
            List of target dictionaries for each image
        """
        pass

class PanopticSegmentationOutputAdapter(OutputAdapter):
    """Adapter for panoptic segmentation model outputs."""
    
    def adapt_output(self, outputs: Dict[str, Any]) -> Dict[str, Any]:
        """Adapt panoptic segmentation outputs to standard format.
        
        Args:
            outputs: Raw model outputs
            
        Returns:
            Adapted outputs in standard format
        """
        loss = getattr(outputs, "loss", None)
        if loss is None and isinstance(outputs, dict):
            loss = outputs.get("loss")
        
        return {
            "loss": loss,
            "logits": outputs.logits,
            "pred_boxes": outputs.pred_boxes,
            "pred_masks": outputs.pred_masks,
            "pred_panoptic_seg": outputs.pred_panoptic_seg,
            "loss_dict": getattr(outputs, "loss_dict", {})
        }
    
    def adapt_targets(self, targets: Dict[str, torch.Tensor]) -> Dict[str, torch.Tensor]:
        """Adapt targets to panoptic segmentation format.
        
        Args:
            targets: Standard format targets
            
        Returns:
            Adapted targets in panoptic segmentation format
        """
        # For panoptic segmentation, we expect panoptic_masks and bounding_boxes
        adapted_targets = {}
        
        if "panoptic_masks" in targets:
            adapted_targets["labels"] = targets["panoptic_masks"]
        elif "labels" in targets:
            adapted_targets["labels"] = targets["labels"]
        
        if "bounding_boxes" in targets:
            adapted_targets["boxes"] = targets["bounding_boxes"]
        
        if "class_labels" in targets:
            adapted_targets["class_labels"] = targets["class_labels"]
        
        return adapted_targets
    
    def format_predictions(self, outputs: Dict[str, Any]) -> List[Dict[str, torch.Tensor]]:
        """Format model outputs for metric computation.
        
        Args:
            outputs: Model outputs dictionary
            
        Returns:
            List of prediction dictionaries for each image
        """
        logits = outputs["logits"]
        pred_boxes = outputs["pred_boxes"]
        pred_masks = outputs["pred_masks"]
        pred_panoptic_seg = outputs["pred_panoptic_seg"]
        
        # Convert to list of dictionaries
        result = []
        for i in range(logits.shape[0]):
            result.append({
                "panoptic_masks": pred_panoptic_seg[i],
                "instance_masks": pred_masks[i],
                "bounding_boxes": pred_boxes[i],
                "class_predictions": torch.argmax(logits[i], dim=-1)
            })
        
        return result
    
    def format_targets(self, targets: Dict[str, torch.Tensor]) -> List[Dict[str, torch.Tensor]]:
        """Format targets for metric computation.
        
        Args:
            targets: Standard format targets
            
        Returns:
            List of target dictionaries for each image
        """
        # Get panoptic masks and bounding boxes
        if "panoptic_masks" in targets:
            masks = targets["panoptic_masks"]
        elif "labels" in targets:
            masks = targets["labels"]
        else:
            raise ValueError("Targets must contain 'panoptic_masks' or 'labels'")
        
        boxes = targets.get("bounding_boxes", None)
        class_labels = targets.get("class_labels", None)
        
        # Convert to list of dictionaries
        result = []
        for i in range(masks.shape[0]):
            target_dict = {
                "panoptic_masks": masks[i]
            }
            if boxes is not None:
                target_dict["bounding_boxes"] = boxes[i]
            if class_labels is not None:
                target_dict["class_labels"] = class_labels[i]
            result.append(target_dict)
        
        return result

def get_input_adapter(model_name: str, image_size: int = 512) -> BaseAdapter:
    """Get the appropriate input adapter for a given model name.
    
    Args:
        model_name: Name of the model
        image_size: Target image size
        
    Returns:
        Appropriate input adapter instance
    """
    model_name_lower = model_name.lower()
    
    if "mask2former" in model_name_lower:
        return Mask2FormerInputAdapter(model_name, image_size)
    else:
        # Default to no-op adapter for other models
        return NoOpInputAdapter()

def get_output_adapter(model_name: str) -> OutputAdapter:
    """Get the appropriate output adapter for a given model name.
    
    Args:
        model_name: Name of the model
        
    Returns:
        Appropriate output adapter instance
    """
    # For panoptic segmentation, we use a single output adapter
    # since the output format is consistent across models
    return PanopticSegmentationOutputAdapter()

# Keep the old function for backward compatibility
def get_panoptic_adapter(model_name: str, image_size: int = 512) -> BaseAdapter:
    """Get the appropriate adapter for a given model name.
    
    Args:
        model_name: Name of the model
        image_size: Target image size
        
    Returns:
        Appropriate adapter instance
    """
    return get_input_adapter(model_name, image_size) 