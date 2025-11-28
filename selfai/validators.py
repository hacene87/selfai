"""Validators for plans and status transitions."""
import json
import re
from pathlib import Path
from typing import Dict, Any, Optional
from .exceptions import PlanValidationError, InvalidStatusTransitionError


class PlanValidator:
    """Validates plan structure and content."""

    REQUIRED_FIELDS = ['description', 'files_to_modify']

    @staticmethod
    def validate_plan_structure(plan: str) -> Dict[str, Any]:
        """Validate plan JSON structure.

        Args:
            plan: Plan content as string

        Returns:
            Parsed plan as dict

        Raises:
            PlanValidationError: If plan is invalid
        """
        if not plan or not plan.strip():
            raise PlanValidationError("Plan is empty")

        try:
            plan_dict = json.loads(plan)
        except json.JSONDecodeError as e:
            raise PlanValidationError(f"Invalid JSON in plan: {e}")

        if not isinstance(plan_dict, dict):
            raise PlanValidationError("Plan must be a JSON object")

        for field in PlanValidator.REQUIRED_FIELDS:
            if field not in plan_dict:
                raise PlanValidationError(f"Missing required field: {field}")

        if not plan_dict['description']:
            raise PlanValidationError("Plan description is empty")

        return plan_dict

    @staticmethod
    def validate_file_paths(plan_dict: Dict[str, Any], repo_path: Path) -> None:
        """Validate file paths in plan are within repository.

        Args:
            plan_dict: Parsed plan dictionary
            repo_path: Repository root path

        Raises:
            PlanValidationError: If paths are invalid
        """
        files = plan_dict.get('files_to_modify', [])

        if not isinstance(files, list):
            raise PlanValidationError("files_to_modify must be a list")

        for file_path_str in files:
            if not isinstance(file_path_str, str):
                raise PlanValidationError(f"File path must be string: {file_path_str}")

            try:
                file_path = Path(file_path_str)

                if file_path.is_absolute():
                    resolved = file_path.resolve()
                else:
                    resolved = (repo_path / file_path).resolve()

                if not str(resolved).startswith(str(repo_path.resolve())):
                    raise PlanValidationError(
                        f"File path outside repository: {file_path_str}"
                    )

                if '..' in file_path.parts:
                    raise PlanValidationError(
                        f"Path traversal detected: {file_path_str}"
                    )

            except (ValueError, OSError) as e:
                raise PlanValidationError(f"Invalid file path {file_path_str}: {e}")

    @staticmethod
    def validate_dependencies(plan_dict: Dict[str, Any]) -> None:
        """Validate plan dependencies are well-formed.

        Args:
            plan_dict: Parsed plan dictionary

        Raises:
            PlanValidationError: If dependencies are invalid
        """
        if 'dependencies' in plan_dict:
            deps = plan_dict['dependencies']
            if not isinstance(deps, list):
                raise PlanValidationError("dependencies must be a list")


class StatusTransitionValidator:
    """Validates status transitions for improvements."""

    VALID_TRANSITIONS = {
        'pending': {'in_progress', 'completed'},
        'in_progress': {'testing', 'pending', 'completed'},
        'testing': {'completed', 'pending'},
        'completed': {'pending'}
    }

    @staticmethod
    def validate_transition(current_status: str, new_status: str) -> None:
        """Validate status transition is allowed.

        Args:
            current_status: Current status
            new_status: Desired new status

        Raises:
            InvalidStatusTransitionError: If transition is invalid
        """
        if not current_status:
            return

        if current_status not in StatusTransitionValidator.VALID_TRANSITIONS:
            raise InvalidStatusTransitionError(
                f"Unknown current status: {current_status}"
            )

        allowed = StatusTransitionValidator.VALID_TRANSITIONS[current_status]

        if new_status not in allowed:
            raise InvalidStatusTransitionError(
                f"Invalid transition from '{current_status}' to '{new_status}'. "
                f"Allowed transitions: {', '.join(sorted(allowed))}"
            )
