/**
 * An error response from the gateway. `reason` is the machine-readable rejection (`model_not_allowed`,
 * `team_settings_conflict`, ...) and `requestId` is the ID to quote when looking the call up in the audit trail.
 */
export class GatewayError extends Error {
  readonly statusCode: number;
  readonly reason?: string;
  readonly requestId?: string;
  /** Seconds from a delta-seconds `Retry-After` header, when the gateway sent one. */
  readonly retryAfter?: number;

  constructor(readonly response: Response, readonly detail: Record<string, unknown>) {
    const reason = typeof detail.reason === 'string' ? detail.reason : undefined;
    const message = typeof detail.message === 'string' ? detail.message : 'Gateway request failed';
    super(`${response.status} ${message}${reason ? ` (${reason})` : ''}`);
    this.name = 'GatewayError';
    this.statusCode = response.status;
    this.reason = reason;
    this.requestId = typeof detail.request_id === 'string'
      ? detail.request_id : response.headers.get('X-Request-ID') ?? undefined;
    const delay = response.headers.get('Retry-After')?.trim();
    if (delay && /^\d+$/.test(delay)) this.retryAfter = Number(delay);
  }
}

/** Thrown without further retries when `Retry-After` exceeds the client's `retryAfterCap`. */
export class GatewayRetryAfterError extends GatewayError {
  declare readonly retryAfter: number;

  constructor(error: GatewayError) {
    super(error.response, error.detail);
    this.name = 'GatewayRetryAfterError';
  }
}

/**
 * The gateway could not be reached or returned an unreadable response. The message never contains the
 * request URL or credentials; `code` is the system error code (such as `ECONNREFUSED`) when one is known.
 */
export class GatewayTransportError extends Error {
  readonly code?: string;

  constructor(code?: string, message?: string) {
    super(message ?? `Cannot reach the gateway${code ? ` (${code})` : ''}; check AGENTWORKFLOWS_URL and gateway health.`);
    this.name = 'GatewayTransportError';
    if (code) this.code = code;
  }
}

/** A request attempt took longer than the client's `timeoutMs`. */
export class GatewayTimeoutError extends GatewayTransportError {
  constructor(readonly timeoutMs: number) {
    super('ETIMEDOUT', `The gateway did not answer within ${timeoutMs / 1000} seconds; check gateway health or raise timeoutMs.`);
    this.name = 'GatewayTimeoutError';
  }
}

/**
 * The gateway ended a stream with a terminal `error` event after sending headers, so the streamed text is
 * incomplete. `error` is the event's error object.
 */
export class GatewayStreamError extends Error {
  readonly reason?: string;

  constructor(readonly error: Record<string, unknown>) {
    super(typeof error.message === 'string' && error.message ? error.message : 'Gateway stream failed');
    this.name = 'GatewayStreamError';
    if (typeof error.reason === 'string') this.reason = error.reason;
  }
}
