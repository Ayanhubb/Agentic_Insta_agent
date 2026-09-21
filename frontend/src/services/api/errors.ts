export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly requestId: string | null;
  readonly details: unknown;

  constructor(
    message: string,
    options: {
      status: number;
      code?: string;
      requestId?: string | null;
      details?: unknown;
    },
  ) {
    super(message);
    this.name = "ApiError";
    this.status = options.status;
    this.code = options.code ?? statusToCode(options.status);
    this.requestId = options.requestId ?? null;
    this.details = options.details;
  }
}

export function statusToCode(status: number): string {
  switch (status) {
    case 401:
      return "AUTHENTICATION_ERROR";
    case 403:
      return "PERMISSION_ERROR";
    case 404:
      return "NOT_FOUND";
    case 409:
      return "CONFLICT";
    case 413:
      return "FILE_TOO_LARGE";
    case 415:
      return "UNSUPPORTED_FORMAT";
    case 422:
      return "VALIDATION_ERROR";
    case 429:
      return "RATE_LIMITED";
    case 500:
      return "INTERNAL_ERROR";
    case 502:
      return "API_ERROR";
    case 503:
      return "CONFIGURATION_ERROR";
    case 504:
      return "TIMEOUT";
    default:
      return "REQUEST_FAILED";
  }
}

export function userMessageFor(error: unknown): string {
  if (error instanceof ApiError) {
    switch (error.status) {
      case 401:
        if (/invalid email or password/i.test(error.message)) {
          return "Invalid email or password.";
        }
        return "Your session has expired. Please sign in again.";
      case 403:
        return "You do not have permission to do that.";
      case 404:
        return error.message || "This resource is not available yet.";
      case 422:
        return error.message || "Please check the form and try again.";
      case 500:
        return "The server ran into a problem. Try again in a moment.";
      default:
        return error.message;
    }
  }
  if (error instanceof Error) return error.message;
  return "Something went wrong.";
}

export function isNotImplemented(error: unknown): boolean {
  return error instanceof ApiError && (error.status === 404 || error.status === 405);
}
