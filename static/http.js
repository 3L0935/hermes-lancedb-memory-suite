(function (root, factory) {
  const api = factory(root);
  if (typeof module === 'object' && module.exports) module.exports = api;
  Object.assign(root, api);
})(typeof globalThis === 'object' ? globalThis : this, function (root) {
  class HttpResponseError extends Error {
    constructor(message, status, code = '') {
      super(message);
      this.name = 'HttpResponseError';
      this.status = status;
      this.code = code;
    }
  }

  class BusyHttpError extends HttpResponseError {
    constructor(status = 503) {
      super('Server busy. Retry failed; try again shortly.', status, 'heavy_work_busy');
      this.name = 'BusyHttpError';
    }
  }

  function retryAfterMilliseconds(response) {
    const raw = response.headers?.get('Retry-After');
    if (raw != null && raw !== '') {
      const seconds = Number(raw);
      if (Number.isFinite(seconds) && seconds >= 0) return seconds * 1000;
      const date = Date.parse(raw);
      if (Number.isFinite(date)) return Math.max(0, date - Date.now());
    }
    return 1000;
  }

  async function errorPayload(response) {
    try {
      const payload = await response.json();
      return payload && typeof payload === 'object' ? payload : {};
    } catch (_error) {
      return {};
    }
  }

  async function fetchJsonWithBusyRetry(url, options = {}) {
    const fetchImpl = options.fetchImpl || root.fetch;
    const sleep = options.sleep || ((milliseconds) => new Promise(
      (resolve) => setTimeout(resolve, milliseconds)
    ));
    const onBusy = options.onBusy || (() => {});
    const maxBusyRetries = options.maxBusyRetries ?? 1;
    const fetchOptions = options.fetchOptions;

    for (let attempt = 0; ; attempt += 1) {
      const response = await fetchImpl(url, fetchOptions);
      if (response.ok) return response.json();

      const payload = await errorPayload(response);
      const busy = response.status === 503 && payload.code === 'heavy_work_busy';
      if (busy && attempt < maxBusyRetries) {
        const retryAfterMs = retryAfterMilliseconds(response);
        onBusy({ attempt: attempt + 1, retryAfterMs });
        await sleep(retryAfterMs);
        continue;
      }
      if (busy) throw new BusyHttpError(response.status);

      const detail = typeof payload.error === 'string'
        ? payload.error
        : 'Request failed';
      throw new HttpResponseError(
        detail + ' (HTTP ' + response.status + ')',
        response.status,
        typeof payload.code === 'string' ? payload.code : '',
      );
    }
  }

  return {
    BusyHttpError,
    HttpResponseError,
    fetchJsonWithBusyRetry,
    retryAfterMilliseconds,
  };
});
