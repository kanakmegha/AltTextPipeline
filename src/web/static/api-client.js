/**
 * Centralized API Request Helper & Single-User CSRF Integration
 * -------------------------------------------------------------
 * - Single-User MVP Mode: No login, registration, or logout screen is presented.
 * - Uses credentials: "include" for all API requests.
 * - Obtains and caches CSRF tokens from /api/auth/csrf-token in memory only
 *   (never stores session or CSRF tokens in localStorage or sessionStorage).
 * - Sends X-CSRF-Token exclusively on state-changing requests (POST, PUT, PATCH, DELETE).
 * - Avoids adding X-CSRF-Token to safe GET/HEAD/OPTIONS requests.
 * - Automatically refreshes the CSRF token on HTTP 403 and displays a clear error if rejected.
 */
(function () {
    "use strict";

    const CSRF_COOKIE_NAME = "alttext_csrf";
    const CSRF_HEADER_NAME = "X-CSRF-Token";
    const SAFE_METHODS = new Set(["GET", "HEAD", "OPTIONS", "TRACE"]);
    const originalFetch = window.fetch.bind(window);

    let csrfTokenCache = null;
    let csrfFetchPromise = null;
    let currentSessionState = {
        authEnabled: false,
        singleUserMode: true,
        user: null,
    };

    function readCookie(name) {
        if (!document.cookie) return null;
        const parts = document.cookie.split(";");
        for (let i = 0; i < parts.length; i++) {
            const part = parts[i].trim();
            if (part.startsWith(name + "=")) {
                return decodeURIComponent(part.substring(name.length + 1));
            }
        }
        return null;
    }

    function isApiUrl(input) {
        try {
            const urlStr = typeof input === "string" ? input : (input && input.url ? input.url : String(input));
            if (urlStr.startsWith("/api/")) return true;
            const parsed = new URL(urlStr, window.location.origin);
            return parsed.origin === window.location.origin && parsed.pathname.startsWith("/api/");
        } catch (_e) {
            return false;
        }
    }

    async function fetchCsrfToken(forceRefresh = false) {
        if (!forceRefresh) {
            const cookieVal = readCookie(CSRF_COOKIE_NAME);
            if (cookieVal) {
                csrfTokenCache = cookieVal;
                return csrfTokenCache;
            }
            if (csrfTokenCache) {
                return csrfTokenCache;
            }
        }

        if (csrfFetchPromise) {
            return csrfFetchPromise;
        }

        csrfFetchPromise = (async () => {
            try {
                const resp = await originalFetch("/api/auth/csrf-token", {
                    method: "GET",
                    credentials: "include",
                    headers: { "Accept": "application/json" },
                });
                if (resp.ok) {
                    const data = await resp.json();
                    if (data && data.csrf_token) {
                        csrfTokenCache = data.csrf_token;
                        return csrfTokenCache;
                    }
                }
            } catch (_err) {
                // Fallback to cookie if present
            } finally {
                csrfFetchPromise = null;
            }
            return readCookie(CSRF_COOKIE_NAME) || csrfTokenCache;
        })();

        return csrfFetchPromise;
    }

    function showSecurityAlert(message, type) {
        if (!document.body) return;
        let banner = document.getElementById("security-alert-banner");
        if (!banner) {
            banner = document.createElement("div");
            banner.id = "security-alert-banner";
            banner.setAttribute("role", "alert");
            banner.style.cssText = [
                "position: fixed",
                "bottom: 20px",
                "right: 20px",
                "max-width: 420px",
                "padding: 12px 16px",
                "border-radius: 8px",
                "font-size: 13px",
                "font-weight: 500",
                "z-index: 9999",
                "box-shadow: 0 8px 24px rgba(0, 0, 0, 0.18)",
                "display: flex",
                "align-items: center",
                "justify-content: space-between",
                "gap: 12px",
            ].join(";");
            document.body.appendChild(banner);
        }

        banner.style.backgroundColor = type === "error" ? "#fef2f2" : "#fffbeb";
        banner.style.color = type === "error" ? "#991b1b" : "#92400e";
        banner.style.border = type === "error" ? "1px solid #f87171" : "1px solid #fbbf24";

        banner.textContent = "";
        const span = document.createElement("span");
        span.textContent = message;
        const closeBtn = document.createElement("button");
        closeBtn.type = "button";
        closeBtn.textContent = "Dismiss";
        closeBtn.style.cssText = "background: transparent; border: none; color: inherit; cursor: pointer; font-weight: 600; text-decoration: underline;";
        closeBtn.onclick = function () {
            banner.remove();
        };

        banner.appendChild(span);
        banner.appendChild(closeBtn);
    }

    async function apiFetch(input, init = {}, _isRetry = false) {
        const options = Object.assign({}, init);
        const method = (options.method || "GET").toUpperCase();
        const apiRequest = isApiUrl(input);

        if (apiRequest) {
            options.credentials = "include";
            const headers = new Headers(options.headers || {});

            if (!SAFE_METHODS.has(method)) {
                const token = await fetchCsrfToken(false);
                if (token) {
                    headers.set(CSRF_HEADER_NAME, token);
                }
            } else {
                headers.delete(CSRF_HEADER_NAME);
            }

            options.headers = headers;
        }

        const response = await originalFetch(input, options);

        if (apiRequest) {
            if (response.status === 403 && !SAFE_METHODS.has(method) && !_isRetry) {
                const refreshed = await fetchCsrfToken(true);
                if (refreshed) {
                    const retryHeaders = new Headers(options.headers || {});
                    retryHeaders.set(CSRF_HEADER_NAME, refreshed);
                    options.headers = retryHeaders;
                    const retryResp = await originalFetch(input, options);
                    if (retryResp.status === 403) {
                        showSecurityAlert(
                            "Access denied (HTTP 403): Your security token or permissions are invalid for this action.",
                            "error"
                        );
                    }
                    return retryResp;
                }
            }

            if (response.status === 403) {
                showSecurityAlert(
                    "Access denied (HTTP 403): Invalid CSRF token or unauthorized project access.",
                    "error"
                );
            } else if (response.status === 401) {
                showSecurityAlert(
                    "Access denied (HTTP 401): Single-user operator authorization required.",
                    "error"
                );
            }
        }

        return response;
    }

    async function initSingleUserSession() {
        try {
            await fetchCsrfToken(false);
            const resp = await originalFetch("/api/auth/session", {
                method: "GET",
                credentials: "include",
                headers: { "Accept": "application/json" },
            });
            if (resp.ok) {
                const data = await resp.json().catch(() => ({}));
                currentSessionState = {
                    authEnabled: Boolean(data.auth_enabled),
                    singleUserMode: data.single_user_mode !== false,
                    user: data.user || null,
                };
            }
        } catch (_err) {
            // Non-blocking in offline/local environments
        }
        return currentSessionState;
    }

    // Install centralized fetch wrapper so all existing frontend modules benefit
    window.fetch = function (input, init) {
        if (isApiUrl(input)) {
            return apiFetch(input, init);
        }
        return originalFetch(input, init);
    };

    window.apiFetch = apiFetch;
    window.ApiClient = {
        fetch: apiFetch,
        fetchCsrfToken: fetchCsrfToken,
        checkSession: initSingleUserSession,
    };

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", function () {
            initSingleUserSession();
        });
    } else {
        initSingleUserSession();
    }
})();
