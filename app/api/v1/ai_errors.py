from fastapi import HTTPException, status

from app.services.ai_execution import AIExecutionErrorCode, AIExecutionFailedError

AI_FAILURE_RESPONSES: dict[AIExecutionErrorCode, tuple[int, str]] = {
    AIExecutionErrorCode.SUBSCRIPTION_REQUIRED: (
        status.HTTP_402_PAYMENT_REQUIRED,
        "An active subscription is required to use AI.",
    ),
    AIExecutionErrorCode.SUBSCRIPTION_INACTIVE: (
        status.HTTP_402_PAYMENT_REQUIRED,
        "The current subscription cannot use AI.",
    ),
    AIExecutionErrorCode.ENTITLEMENT_DENIED: (
        status.HTTP_403_FORBIDDEN,
        "AI access is not enabled for the current subscription.",
    ),
    AIExecutionErrorCode.CREDIT_LIMIT_REACHED: (
        status.HTTP_429_TOO_MANY_REQUESTS,
        "The AI response limit has been reached for the current period.",
    ),
    AIExecutionErrorCode.PROVIDER_REJECTED: (
        status.HTTP_422_UNPROCESSABLE_CONTENT,
        "The AI provider rejected this request.",
    ),
    AIExecutionErrorCode.PROVIDER_BLOCKED: (
        status.HTTP_422_UNPROCESSABLE_CONTENT,
        "The AI provider blocked this request.",
    ),
    AIExecutionErrorCode.PROVIDER_INCOMPLETE: (
        status.HTTP_502_BAD_GATEWAY,
        "The AI provider returned an incomplete response.",
    ),
    AIExecutionErrorCode.PROVIDER_INVALID: (
        status.HTTP_502_BAD_GATEWAY,
        "The AI provider returned an invalid response.",
    ),
    AIExecutionErrorCode.RESPONSE_TOO_LARGE: (
        status.HTTP_502_BAD_GATEWAY,
        "The AI provider returned a response that was too large.",
    ),
    AIExecutionErrorCode.PROVIDER_CONFIGURATION: (
        status.HTTP_503_SERVICE_UNAVAILABLE,
        "AI service is temporarily unavailable.",
    ),
    AIExecutionErrorCode.PROVIDER_TEMPORARY: (
        status.HTTP_503_SERVICE_UNAVAILABLE,
        "AI service is temporarily unavailable.",
    ),
    AIExecutionErrorCode.PERSISTENCE_FAILURE: (
        status.HTTP_503_SERVICE_UNAVAILABLE,
        "AI service could not persist the response.",
    ),
    AIExecutionErrorCode.EXECUTION_INTEGRITY: (
        status.HTTP_503_SERVICE_UNAVAILABLE,
        "AI service is temporarily unavailable.",
    ),
}


def ai_execution_http_error(exc: AIExecutionFailedError) -> HTTPException:
    status_code, detail = AI_FAILURE_RESPONSES[exc.error_code]
    return HTTPException(status_code=status_code, detail=detail)


__all__ = ["AI_FAILURE_RESPONSES", "ai_execution_http_error"]
