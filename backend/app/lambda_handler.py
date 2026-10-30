"""AWS Lambda entry point (API Gateway HTTP API, payload v2) for the FastAPI app.

Mangum returns the response only after Starlette's background tasks finish, so an investigation
started through POST completes inside the invocation. That is why the Lambda timeout is kept under
API Gateway's 30s limit and why state lives in DynamoDB rather than in the container.
"""

from mangum import Mangum

from app.main import app

handler = Mangum(app, lifespan="off")
