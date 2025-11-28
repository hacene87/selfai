"""HTTP Server for SelfAI Dashboard with API endpoints."""
import json
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
import logging

from .database import Database
from .runner import SelfAIRunner

logger = logging.getLogger('selfai')

class DashboardHandler(BaseHTTPRequestHandler):
    """Handle dashboard requests and API calls."""

    def __init__(self, *args, repo_path=None, **kwargs):
        self.repo_path = repo_path or Path.cwd()
        self.data_dir = self.repo_path / '.selfai_data'
        self.db = Database(self.data_dir / 'data' / 'improvements.db')
        super().__init__(*args, **kwargs)

    def log_message(self, format, *args):
        """Suppress default logging."""
        pass

    def send_json(self, data, status=200):
        """Send JSON response."""
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def send_html(self, html):
        """Send HTML response."""
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write(html.encode())

    def do_OPTIONS(self):
        """Handle CORS preflight."""
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def do_GET(self):
        """Handle GET requests."""
        path = urllib.parse.urlparse(self.path).path

        if path == '/' or path == '/dashboard':
            # Serve dashboard
            runner = SelfAIRunner(self.repo_path)
            runner.update_dashboard()
            dashboard_path = self.data_dir / 'dashboard.html'
            if dashboard_path.exists():
                self.send_html(dashboard_path.read_text())
            else:
                self.send_json({'error': 'Dashboard not found'}, 404)

        elif path == '/api/status':
            # Get status
            stats = self.db.get_stats()
            self.send_json(stats)

        elif path == '/api/tasks':
            # Get all tasks
            tasks = self.db.get_all()
            self.send_json(tasks)

        elif path.startswith('/api/task/'):
            # Get single task
            try:
                task_id = int(path.split('/')[-1])
                task = self.db.get_by_id(task_id)
                if task:
                    self.send_json(task)
                else:
                    self.send_json({'error': 'Task not found'}, 404)
            except ValueError:
                self.send_json({'error': 'Invalid task ID'}, 400)

        else:
            self.send_json({'error': 'Not found'}, 404)

    def do_POST(self):
        """Handle POST requests."""
        path = urllib.parse.urlparse(self.path).path

        # Read body if present
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length).decode() if content_length > 0 else '{}'
        try:
            data = json.loads(body) if body else {}
        except json.JSONDecodeError:
            data = {}

        if path.startswith('/api/approve/'):
            # Approve a plan
            try:
                task_id = int(path.split('/')[-1])
                task = self.db.get_by_id(task_id)
                if not task:
                    self.send_json({'error': 'Task not found'}, 404)
                    return

                if task['status'] != 'plan_review':
                    self.send_json({'error': f"Task is not in plan_review status (current: {task['status']})"}, 400)
                    return

                self.db.approve_plan(task_id)
                logger.info(f"Approved plan for task #{task_id}")
                self.send_json({'success': True, 'message': f'Task #{task_id} approved for execution'})
            except ValueError:
                self.send_json({'error': 'Invalid task ID'}, 400)

        elif path.startswith('/api/feedback/'):
            # Provide feedback
            try:
                task_id = int(path.split('/')[-1])
                feedback = data.get('feedback', '')

                if not feedback:
                    self.send_json({'error': 'Feedback is required'}, 400)
                    return

                task = self.db.get_by_id(task_id)
                if not task:
                    self.send_json({'error': 'Task not found'}, 404)
                    return

                self.db.request_plan_feedback(task_id, feedback)
                logger.info(f"Feedback submitted for task #{task_id}")
                self.send_json({'success': True, 'message': f'Feedback submitted for task #{task_id}'})
            except ValueError:
                self.send_json({'error': 'Invalid task ID'}, 400)

        elif path.startswith('/api/reenable/'):
            # Re-enable cancelled task
            try:
                task_id = int(path.split('/')[-1])
                feedback = data.get('feedback', '')

                task = self.db.get_by_id(task_id)
                if not task:
                    self.send_json({'error': 'Task not found'}, 404)
                    return

                if task['status'] != 'cancelled':
                    self.send_json({'error': f"Task is not cancelled (current: {task['status']})"}, 400)
                    return

                self.db.re_enable_cancelled(task_id, feedback)
                logger.info(f"Re-enabled task #{task_id}")
                self.send_json({'success': True, 'message': f'Task #{task_id} re-enabled'})
            except ValueError:
                self.send_json({'error': 'Invalid task ID'}, 400)

        else:
            self.send_json({'error': 'Not found'}, 404)


def create_handler(repo_path):
    """Create a handler class with repo_path bound."""
    class BoundHandler(DashboardHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, repo_path=repo_path, **kwargs)
    return BoundHandler


def run_server(host='localhost', port=8787, repo_path=None):
    """Run the dashboard server."""
    if repo_path is None:
        repo_path = Path.cwd()

    handler = create_handler(repo_path)
    server = HTTPServer((host, port), handler)

    print(f"\n  SelfAI Dashboard Server")
    print(f"  http://{host}:{port}/")
    print(f"  Press Ctrl+C to stop\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped")
        server.shutdown()


if __name__ == '__main__':
    run_server()
