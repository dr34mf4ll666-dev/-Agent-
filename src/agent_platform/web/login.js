"use strict";

const form = document.querySelector("#login-form");
const errorBox = document.querySelector("#login-error");

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = form.querySelector("button");
  button.disabled = true;
  errorBox.hidden = true;
  try {
    const response = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: form.username.value.trim(), password: form.password.value }),
    });
    let payload;
    try { payload = await response.json(); } catch { payload = { error: "登录服务返回了无法识别的内容。" }; }
    if (!response.ok) throw new Error(payload.error || "登录失败。");
    window.location.assign(payload.destination || "/");
  } catch (error) {
    errorBox.textContent = error.message;
    errorBox.hidden = false;
    form.password.select();
  } finally { button.disabled = false; }
});
