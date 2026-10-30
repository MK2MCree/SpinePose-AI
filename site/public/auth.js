(() => {
    const REMEMBERED_ACCOUNTS_KEY = "mindspine.remembered_accounts";
    const MAX_REMEMBERED_ACCOUNTS = 6;

    const byId = (id) => document.getElementById(id);

    async function api(url, options = {}) {
        let response;
        try {
            response = await fetch(url, { credentials: "same-origin", ...options });
        } catch {
            throw new Error("账户服务暂时不可用，请确认服务已启动后重试");
        }
        const contentType = response.headers.get("content-type") || "";
        const payload = contentType.includes("application/json") ? await response.json() : {};
        if (!response.ok) {
            throw new Error(payload.message || payload.detail || `账户服务接口不可用（HTTP ${response.status}）`);
        }
        return payload;
    }

    function rememberedAccounts() {
        try {
            const value = JSON.parse(localStorage.getItem(REMEMBERED_ACCOUNTS_KEY) || "[]");
            return Array.isArray(value) ? value.filter((account) => account && account.username) : [];
        } catch {
            return [];
        }
    }

    function rememberAccount(user) {
        if (!user || !user.username) return;
        const accounts = rememberedAccounts().filter((account) => account.id !== user.id);
        accounts.unshift({ id: user.id, username: user.username, avatar_url: user.avatar_url || null });
        localStorage.setItem(REMEMBERED_ACCOUNTS_KEY, JSON.stringify(accounts.slice(0, MAX_REMEMBERED_ACCOUNTS)));
    }

    function initials(username) {
        return Array.from((username || "脊").trim()).slice(0, 1).join("").toUpperCase() || "脊";
    }

    function escapeHtml(value) {
        return String(value || "").replace(/[&<>'"]/g, (character) => ({
            "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
        }[character]));
    }

    function safeAvatarUrl(value) {
        return /^\/api\/auth\/avatar\/[0-9a-f]{32}$/.test(value || "") ? value : "";
    }

    function avatarMarkup(user, extraClass = "") {
        const label = user?.username || "用户";
        const avatarUrl = safeAvatarUrl(user?.avatar_url);
        if (avatarUrl) {
            return `<img class="${extraClass}" src="${avatarUrl}" alt="${escapeHtml(label)} 的头像">`;
        }
        return `<span class="${extraClass} auth-avatar-fallback" aria-hidden="true">${escapeHtml(initials(label))}</span>`;
    }

    function setMessage(element, message = "", tone = "") {
        if (!element) return;
        element.textContent = message;
        element.className = `auth-message ${tone}`;
    }

    function loginUrl(username = "") {
        return `login.html${username ? `?username=${encodeURIComponent(username)}` : ""}`;
    }

    function renderAccountChoices(container, accounts) {
        if (!container) return;
        if (!accounts.length) {
            container.innerHTML = "";
            container.hidden = true;
            return;
        }
        container.hidden = false;
        container.innerHTML = accounts.map((account) => `
            <button class="account-choice" type="button" data-account-name="${escapeHtml(account.username)}">
                ${avatarMarkup(account, "account-choice-avatar")}
                <span><strong>${escapeHtml(account.username)}</strong><small>点击后输入密码</small></span>
                <i class="fa-solid fa-chevron-right" aria-hidden="true"></i>
            </button>
        `).join("");
        container.querySelectorAll("[data-account-name]").forEach((button) => {
            button.addEventListener("click", () => {
                window.location.href = loginUrl(button.dataset.accountName || "");
            });
        });
    }

    async function currentUser() {
        const payload = await api("/api/auth/me");
        return payload.user;
    }

    async function initHome() {
        const guestActions = byId("guest-actions");
        const memberActions = byId("member-actions");
        const memberEntryActions = document.querySelectorAll("[id^='member-'][id$='-actions']");
        try {
            const user = await currentUser();
            rememberAccount(user);
            if (guestActions) guestActions.hidden = true;
            if (memberActions) memberActions.hidden = false;
            memberEntryActions.forEach((element) => { element.hidden = false; });
            const avatarButton = byId("account-avatar-button");
            const accountName = byId("account-menu-name");
            if (avatarButton) {
                avatarButton.innerHTML = avatarMarkup(user, "account-nav-avatar");
                avatarButton.setAttribute("aria-label", `${user.username} 的账号菜单`);
            }
            if (accountName) accountName.textContent = user.username;
            renderAccountChoices(byId("account-menu-list"), rememberedAccounts());
            bindAccountMenu();
        } catch {
            if (guestActions) guestActions.hidden = false;
            if (memberActions) memberActions.hidden = true;
            memberEntryActions.forEach((element) => { element.hidden = true; });
        }
    }

    function bindAccountMenu() {
        const button = byId("account-avatar-button");
        const menu = byId("account-menu");
        if (!button || !menu || button.dataset.bound) return;
        button.dataset.bound = "true";
        button.addEventListener("click", () => {
            const isOpen = !menu.hidden;
            menu.hidden = isOpen;
            button.setAttribute("aria-expanded", String(!isOpen));
        });
        document.addEventListener("click", (event) => {
            if (!menu.hidden && !menu.contains(event.target) && !button.contains(event.target)) {
                menu.hidden = true;
                button.setAttribute("aria-expanded", "false");
            }
        });
        const logoutButton = byId("account-logout");
        if (logoutButton) {
            logoutButton.addEventListener("click", async () => {
                logoutButton.disabled = true;
                try {
                    await api("/api/auth/logout", { method: "POST" });
                    window.location.href = "index.html";
                } catch (error) {
                    window.alert(error.message || "退出失败，请稍后重试");
                    logoutButton.disabled = false;
                }
            });
        }
    }

    function initLogin() {
        const form = byId("login-form");
        if (!form) return;
        const usernameInput = byId("login-username");
        const passwordInput = byId("login-password");
        const submitButton = byId("login-submit");
        const message = byId("login-message");
        const queryUsername = new URLSearchParams(window.location.search).get("username");
        if (queryUsername && usernameInput) {
            usernameInput.value = queryUsername;
            passwordInput?.focus();
        }
        renderAccountChoices(byId("login-account-choices"), rememberedAccounts());
        form.addEventListener("submit", async (event) => {
            event.preventDefault();
            setMessage(message);
            submitButton.disabled = true;
            submitButton.textContent = "正在登录…";
            const formData = new FormData();
            formData.set("username", usernameInput.value.trim());
            formData.set("password", passwordInput.value);
            try {
                const payload = await api("/api/auth/login", { method: "POST", body: formData });
                rememberAccount(payload.user);
                window.location.href = "index.html";
            } catch (error) {
                setMessage(message, error.message || "登录失败，请重试", "error");
                submitButton.disabled = false;
                submitButton.textContent = "登录";
            }
        });
    }

    function initRegister() {
        const form = byId("register-form");
        if (!form) return;
        const avatarInput = byId("register-avatar");
        const avatarPreview = byId("avatar-preview");
        const avatarPlaceholder = byId("avatar-placeholder");
        const submitButton = byId("register-submit");
        const message = byId("register-message");
        let previewUrl = "";
        avatarInput?.addEventListener("change", () => {
            const file = avatarInput.files?.[0];
            if (previewUrl) URL.revokeObjectURL(previewUrl);
            previewUrl = "";
            if (!file) {
                avatarPreview.hidden = true;
                avatarPlaceholder.hidden = false;
                return;
            }
            previewUrl = URL.createObjectURL(file);
            avatarPreview.src = previewUrl;
            avatarPreview.hidden = false;
            avatarPlaceholder.hidden = true;
        });
        form.addEventListener("submit", async (event) => {
            event.preventDefault();
            setMessage(message);
            const password = byId("register-password").value;
            const confirm = byId("register-password-confirm").value;
            if (password !== confirm) {
                setMessage(message, "两次输入的密码不一致", "error");
                return;
            }
            submitButton.disabled = true;
            submitButton.textContent = "正在创建…";
            const formData = new FormData(form);
            try {
                const payload = await api("/api/auth/register", { method: "POST", body: formData });
                rememberAccount(payload.user);
                window.location.href = "index.html";
            } catch (error) {
                setMessage(message, error.message || "注册失败，请重试", "error");
                submitButton.disabled = false;
                submitButton.textContent = "创建账号";
            }
        });
    }

    async function requireUser() {
        try {
            const user = await currentUser();
            rememberAccount(user);
            return true;
        } catch {
            window.location.replace("login.html");
            return false;
        }
    }

    window.MindSpineAuth = { api, requireUser, currentUser };
    document.addEventListener("DOMContentLoaded", () => {
        const page = document.body?.dataset.page;
        if (page === "home") initHome();
        if (page === "login") initLogin();
        if (page === "register") initRegister();
    });
})();
