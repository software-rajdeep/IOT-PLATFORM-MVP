import { api } from "../api.js";
import { store } from "../store.js";
import { esc } from "../ui.js";

export function renderLogin(view, ctx) {
  view.innerHTML = `
    <div class="login-wrap">
      <div class="login-card">
        <div class="brand" style="margin-bottom:18px">
          <span class="dot">U</span>
          <span>Universal Platform<small>INDUSTRIAL CONFIG ENGINE</small></span>
        </div>
        <h1>Sign in</h1>
        <p class="lead" style="margin-bottom:18px">One account. Any authorised industrial application.</p>
        <form id="login-form">
          <div class="field">
            <label for="email">Email or username</label>
            <input id="email" type="text" autocomplete="username" value="admin@demo.local" />
          </div>
          <div class="field">
            <label for="password">Password</label>
            <input id="password" type="password" autocomplete="current-password" value="demo1234" />
          </div>
          <div id="login-error"></div>
          <button class="btn" type="submit" style="width:100%">Sign in</button>
        </form>
        <div class="demo-hint">
          demo tenant · admin@demo.local / demo1234
        </div>
      </div>
    </div>`;

  const form = document.getElementById("login-form");
  const errorBox = document.getElementById("login-error");
  const submit = form.querySelector("button");

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    errorBox.innerHTML = "";
    submit.disabled = true;
    submit.textContent = "Signing in…";
    const email = document.getElementById("email").value.trim();
    const password = document.getElementById("password").value;
    try {
      const result = await api.login(email, password);
      store.setSession(result.token, { user: result.user, company: result.company });
      ctx.navigate("#/apps");
    } catch (err) {
      errorBox.innerHTML = `<div class="error-box">${esc(err.message)}</div>`;
      submit.disabled = false;
      submit.textContent = "Sign in";
    }
  });
}
