/**
 * Centralized API Request Helper & Authentication/CSRF Integration
 * ----------------------------------------------------------------
 * - Uses credentials: "include" for all authenticated API requests.
 * - Obtains and caches CSRF tokens from /api/auth/csrf-token in memory only
 *   (never stores session or CSRF tokens in localStorage or sessionStorage).
 * - Sends X-CSRF-Token exclusively on state-changing requests (POST, PUT, PATCH, DELETE).
 * - Avoids adding X-CSRF-Token to safe GET/HEAD/OPTIONS requests.
 * - Automatically refreshes the CSRF token on HTTP 403 and displays a clear error if rejected.
 * - Handles HTTP 401 by presenting an accessible login modal and updating auth state.
 */
(function () {
    "use strict";

    const CSRF_COOKIE_NAME = "alttext_csrf";
    const CSRF_HEADER_NAME = "X-CSRF-Token";
    const SAFE_METHODS = new Set(["GET", "HEAD", "OPTIONS", "TRACE"]);
    const originalFetch = window.fetch.bind(window);

    let csrfTokenCache = null;
    let csrfFetchPromise = null;
    let currentAuthState = {
        authEnabled: false,
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

    function getApiPathname(input) {
        try {
            const urlStr = typeof input === "string" ? input : (input && input.url ? input.url : String(input));
            const parsed = new URL(urlStr, window.location.origin);
            return parsed.pathname;
        } catch (_e) {
            return "";
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

    function ensureLoginModal() {
        let modal = document.getElementById("auth-login-modal");
        if (modal) return modal;

        modal = document.createElement("div");
        modal.id = "auth-login-modal";
        modal.setAttribute("role", "dialog");
        modal.setAttribute("aria-modal", "true");
        modal.setAttribute("aria-labelledby", "auth-login-title");
        modal.style.cssText = [
            "position: fixed",
            "inset: 0",
            "background: rgba(15, 23, 42, 0.65)",
            "display: none",
            "align-items: center",
            "justify-content: center",
            "z-index: 10000",
        ].join(";");

        modal.innerHTML = [
            '<div style="background: #ffffff; color: #0f172a; padding: 28px; border-radius: 12px; width: 100%; max-width: 380px; box-shadow: 0 20px 40px rgba(0,0,0,0.25);">',
            '  <h2 id="auth-login-title" style="margin: 0 0 6px 0; font-size: 18px; font-weight: 700;">Sign In Required</h2>',
            '  <p style="margin: 0 0 18px 0; font-size: 13px; color: #475569;">Please sign in with your editor credentials to access AltTextPipeline.</p>',
            '  <form id="auth-login-form">',
            '    <div style="margin-bottom: 12px;">',
            '      <label for="auth-login-email" style="display: block; font-size: 12px; font-weight: 600; margin-bottom: 4px;">Email</label>',
            '      <input id="auth-login-email" type="email" required autocomplete="username" style="width: 100%; padding: 8px 10px; border: 1px solid #cbd5e1; border-radius: 6px; font-size: 14px; box-sizing: border-box;" />',
            '    </div>',
            '    <div style="margin-bottom: 14px;">',
            '      <label for="auth-login-password" style="display: block; font-size: 12px; font-weight: 600; margin-bottom: 4px;">Password</label>',
            '      <input id="auth-login-password" type="password" required autocomplete="current-password" maxlength="256" style="width: 100%; padding: 8px 10px; border: 1px solid #cbd5e1; border-radius: 6px; font-size: 14px; box-sizing: border-box;" />',
            '    </div>',
            '    <div id="auth-login-error" style="display: none; margin-bottom: 12px; padding: 8px 10px; border-radius: 6px; background: #fef2f2; color: #991b1b; font-size: 12px;"></div>',
            '    <button id="auth-login-submit" type="submit" style="width: 100%; padding: 10px 14px; background: #2563eb; color: #ffffff; border: none; border-radius: 6px; font-size: 14px; font-weight: 600; cursor: pointer;">Sign In</button>',
            '  </form>',
            '</div>',
        ].join("\n");

        document.body.appendChild(modal);

        const form = modal.querySelector("#auth-login-form");
        const errBox = modal.querySelector("#auth-login-error");
        const submitBtn = modal.querySelector("#auth-login-submit");

        form.addEventListener("submit", async function (e) {
            e.preventDefault();
            errBox.style.display = "none";
            submitBtn.disabled = true;
            submitBtn.textContent = "Signing in...";

            const email = modal.querySelector("#auth-login-email").value.trim();
            const password = modal.querySelector("#auth-login-password").value;

            try {
                await fetchCsrfToken(true);
                const resp = await apiFetch("/api/auth/login", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ email: email, password: password }),
                });
                const data = await resp.json().catch(() => ({}));
                if (!resp.ok) {
                    errBox.textContent = data.detail || "Authentication failed.";
                    errBox.style.display = "block";
                    return;
                }
                if (data.csrf_token) {
                    csrfTokenCache = data.csrf_token;
                }
                currentAuthState = {
                    authEnabled: true,
                    user: data.user || { email: email },
                };
                modal.style.display = "none";
                modal.querySelector("#auth-login-password").value = "";
                renderAuthControls();
                window.location.reload();
            } catch (_err) {
                errBox.textContent = "Network error while signing in. Please try again.";
                errBox.style.display = "block";
            } finally {
                submitBtn.disabled = false;
                submitBtn.textContent = "Sign In";
            }
        });

        return modal;
    }

    function showLoginModal() {
        if (!document.body) return;
        const modal = ensureLoginModal();
        modal.style.display = "flex";
        const emailInput = modal.querySelector("#auth-login-email");
        if (emailInput) emailInput.focus();
    }

    function renderAuthControls() {
        if (!currentAuthState.authEnabled || !currentAuthState.user) return;
        let badge = document.getElementById("auth-user-controls");
        if (badge) return;

        const headerRight = document.querySelector(".header-right") || document.querySelector(".top-bar");
        if (!headerRight) return;

        badge = document.createElement("div");
        badge.id = "auth-user-controls";
        badge.style.cssText = "display: inline-flex; align-items: center; gap: 8px; margin-left: 10px; font-size: 12px;";

        const userLabel = document.createElement("span");
        userLabel.textContent = currentAuthState.user.email || "";
        userLabel.style.cssText = "opacity: 0.85;";

        const logoutBtn = document.createElement("button");
        logoutBtn.type = "button";
        logoutBtn.className = "btn btn-secondary btn-sm";
        logoutBtn.textContent = "Sign Out";
        logoutBtn.onclick = async function () {
            await logout();
        };

        badge.appendChild(userLabel);
        badge.appendChild(logoutBtn);
        headerRight.appendChild(badge);
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
            const path = getApiPathname(input);

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
                    "Access denied (HTTP 403): Invalid CSRF token or insufficient permissions for this resource.",
                    "error"
                );
            } else if (
                response.status === 401 &&
                path !== "/api/auth/login" &&
                path !== "/api/auth/session"
            ) {
                showLoginModal();
            }
        }

        return response;
    }

    async function checkSession() {
        try {
            const resp = await originalFetch("/api/auth/session", {
                method: "GET",
                credentials: "include",
                headers: { "Accept": "application/json" },
            });
            const data = await resp.json().catch(() => ({}));
            if (resp.status === 401 || (data && data.auth_enabled && !data.user)) {
                currentAuthState = { authEnabled: true, user: null };
                await fetchCsrfToken(true);
                showLoginModal();
                return currentAuthState;
            }
            if (data && data.auth_enabled) {
                currentAuthState = { authEnabled: true, user: data.user };
                await fetchCsrfToken(false);
                renderAuthControls();
            } else {
                currentAuthState = { authEnabled: false, user: data.user || null };
            }
        } catch (_err) {
            // Non-blocking in offline/local environments
        }
        return currentAuthState;
    }

    async function logout() {
        try {
            await apiFetch("/api/auth/logout", { method: "POST" });
        } finally {
            csrfTokenCache = null;
            currentAuthState.user = null;
            showLoginModal();
        }
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
        checkSession: checkSession,
        logout: logout,
        showLoginModal: showLoginModal,
    };

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", function () {
            checkSession();
        });
    } else {
        checkSession();
    }
})();

