// Action buttons and forms that call the API (P9-T2, T7).

import { Action, OfflineError } from "./api.js";
import { h, toast } from "./dom.js";

/** Show what went wrong with an action, in words staff can act on. */
export function reportError(error) {
  if (error instanceof OfflineError) toast(error.message, "error");
  else toast(error.message || "Something went wrong.", "error");
}

/**
 * A button bound to one Action. While the request runs the button is disabled, so a second
 * tap does nothing; the Action's key makes even a repeated request a no-op on the server.
 * `done(result)` runs after success.
 */
export function actionButton(label, action, done, { kind = "primary", confirm } = {}) {
  const button = h("button", { type: "button", class: kind }, label);
  button.addEventListener("click", async () => {
    if (action.busy || button.disabled) return;
    if (confirm && !window.confirm(confirm)) return;
    button.disabled = true;
    try {
      const result = await action.run();
      if (!result || !result.skipped) await done(result);
    } catch (error) {
      reportError(error);
    } finally {
      button.disabled = false;
    }
  });
  return button;
}

/**
 * A form whose submit sends one request. `check(form)` returns an error message to refuse
 * the submit before anything is sent, or null. The Action (and its key) is made once per
 * form, so resubmitting after a lost answer is safe; it is renewed after a refusal by the
 * server, because nothing was done.
 */
export function actionForm(children, { method, path, body, check, done, submitLabel }) {
  let action = new Action(method, path, () => body(form));
  const submit = h("button", { type: "submit", class: "primary" }, submitLabel);
  const error = h("p", { class: "form-error", role: "alert" });
  const form = h("form", { class: "form", novalidate: true }, children, error, submit);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    error.textContent = "";
    const problem = check ? check(form) : null;
    if (problem) {
      error.textContent = problem;
      return;
    }
    if (action.busy) return;
    submit.disabled = true;
    try {
      const result = await action.run();
      await done(result, form);
      action = new Action(method, path, () => body(form));
    } catch (e) {
      error.textContent = e.message;
      if (!(e instanceof OfflineError)) action = new Action(method, path, () => body(form));
    } finally {
      submit.disabled = false;
    }
  });
  return form;
}
