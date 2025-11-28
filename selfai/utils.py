"""
Utility functions for port and resource management in isolated test environments.
"""
import socket
import os
import threading
import uuid
from typing import List, Optional


# Global registry for allocated ports across all environments
_allocated_ports: set = set()
_port_lock = threading.Lock()


def allocate_ports(count: int, start_port: int = 10000) -> List[int]:
    """
    Allocate unique available ports.

    Args:
        count: Number of ports to allocate
        start_port: Starting port to search from

    Returns:
        List of allocated port numbers

    Raises:
        RuntimeError: If unable to allocate requested number of ports
    """
    ports = []
    current = start_port

    with _port_lock:
        while len(ports) < count and current < 65535:
            if current not in _allocated_ports and is_port_available(current):
                _allocated_ports.add(current)
                ports.append(current)
            current += 1

    if len(ports) < count:
        raise RuntimeError(f"Could not allocate {count} ports")

    return ports


def release_ports(ports: List[int]):
    """
    Release allocated ports back to pool.

    Args:
        ports: List of port numbers to release
    """
    with _port_lock:
        for port in ports:
            _allocated_ports.discard(port)


def is_port_available(port: int) -> bool:
    """
    Check if a port is available for binding.

    Args:
        port: Port number to check

    Returns:
        True if port is available, False otherwise
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(('127.0.0.1', port))
            return True
    except OSError:
        return False


def get_unique_test_id() -> str:
    """
    Generate unique test identifier.

    Returns:
        Unique identifier string combining worker ID and UUID
    """
    worker_id = os.environ.get('PYTEST_XDIST_WORKER', 'main')
    return f"{worker_id}-{uuid.uuid4().hex[:8]}"
