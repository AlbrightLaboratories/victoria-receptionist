/**
 * Victoria Albright — chat widget.
 *
 * Drop-in floating chat bubble for albrightlab.com:
 *
 *   <script src="https://victoria.albrightlab.com/widget.js" defer></script>
 *
 * Self-contained. No jQuery, no framework, no build step. Loads its
 * own stylesheet from /widget.css on the same origin.
 *
 * Exposes:
 *   window.victoria.open()    open the chat panel
 *   window.victoria.close()   close it
 *   window.victoria.toggle()  flip it
 *
 * State is held in a closure; conversation_id round-trips with the
 * server so multi-turn context persists across messages within a
 * page-load.
 */
(function () {
  "use strict";

  // The server origin is whatever served widget.js. This lets staging
  // and prod work without a hardcoded URL.
  const SCRIPT_EL =
    document.currentScript ||
    Array.from(document.scripts).find((s) =>
      (s.src || "").includes("widget.js")
    );
  const SERVER_ORIGIN = SCRIPT_EL
    ? new URL(SCRIPT_EL.src, window.location.href).origin
    : window.location.origin;

  // ------- DOM scaffolding ------------------------------------------------

  function injectStyles() {
    const link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = SERVER_ORIGIN + "/widget.css";
    document.head.appendChild(link);
  }

  function createEl(tag, attrs, children) {
    const el = document.createElement(tag);
    if (attrs) {
      for (const k in attrs) {
        if (k === "className") {
          el.className = attrs[k];
        } else if (k === "html") {
          el.innerHTML = attrs[k];
        } else {
          el.setAttribute(k, attrs[k]);
        }
      }
    }
    (children || []).forEach((c) => {
      if (typeof c === "string") {
        el.appendChild(document.createTextNode(c));
      } else if (c) {
        el.appendChild(c);
      }
    });
    return el;
  }

  let root, bubble, panel, log, input, sendBtn;
  let conversationId = null;
  let isOpen = false;
  let isSending = false;

  function buildDom() {
    bubble = createEl(
      "button",
      {
        className: "victoria-bubble",
        "aria-label": "Open chat with Victoria",
        type: "button",
      },
      ["V"]
    );
    bubble.addEventListener("click", api.toggle);

    const header = createEl("div", { className: "victoria-header" }, [
      createEl("div", { className: "victoria-title" }, [
        createEl("strong", null, ["Victoria Albright"]),
        createEl("span", { className: "victoria-subtitle" }, [
          "AlbrightLab receptionist",
        ]),
      ]),
      createEl(
        "button",
        {
          className: "victoria-close",
          "aria-label": "Close chat",
          type: "button",
        },
        ["x"]
      ),
    ]);
    header.querySelector(".victoria-close").addEventListener("click", api.close);

    log = createEl("div", {
      className: "victoria-log",
      role: "log",
      "aria-live": "polite",
    });

    input = createEl("textarea", {
      className: "victoria-input",
      placeholder: "Ask me anything about AlbrightLab...",
      rows: "2",
      "aria-label": "Your message",
    });
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        sendMessage();
      }
    });

    sendBtn = createEl(
      "button",
      { className: "victoria-send", type: "button" },
      ["Send"]
    );
    sendBtn.addEventListener("click", sendMessage);

    const inputRow = createEl("div", { className: "victoria-input-row" }, [
      input,
      sendBtn,
    ]);

    panel = createEl(
      "div",
      { className: "victoria-panel", role: "dialog", "aria-hidden": "true" },
      [header, log, inputRow]
    );

    root = createEl("div", { className: "victoria-root" }, [panel, bubble]);
    document.body.appendChild(root);

    appendMessage(
      "assistant",
      "Hi, I'm Victoria. Ask me about our ventures, careers, partners, or how to reach the team."
    );
  }

  // ------- Rendering ------------------------------------------------------

  function appendMessage(role, content, citations) {
    const row = createEl("div", {
      className: "victoria-msg victoria-msg-" + role,
    });
    row.appendChild(
      createEl("div", { className: "victoria-msg-content" }, [content])
    );
    if (citations && citations.length) {
      const cites = createEl("div", { className: "victoria-citations" });
      citations.forEach((c) => {
        if (!c.url) return;
        const a = createEl("a", {
          href: c.url,
          target: "_blank",
          rel: "noopener noreferrer",
        });
        a.textContent = c.title || c.url;
        cites.appendChild(a);
      });
      row.appendChild(cites);
    }
    log.appendChild(row);
    log.scrollTop = log.scrollHeight;
    return row;
  }

  function appendTyping() {
    const row = createEl(
      "div",
      { className: "victoria-msg victoria-msg-assistant victoria-typing" },
      ["..."]
    );
    log.appendChild(row);
    log.scrollTop = log.scrollHeight;
    return row;
  }

  // ------- Network --------------------------------------------------------

  async function sendMessage() {
    const text = (input.value || "").trim();
    if (!text || isSending) return;
    isSending = true;
    sendBtn.disabled = true;
    appendMessage("user", text);
    input.value = "";
    const typing = appendTyping();
    try {
      const resp = await fetch(SERVER_ORIGIN + "/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          conversation_id: conversationId,
          message: text,
          page_url: window.location.href,
        }),
      });
      typing.remove();
      if (!resp.ok) {
        appendMessage(
          "assistant",
          "Sorry, I hit an error. Please email coreymalbright@gmail.com or call (202) 642-6739."
        );
        return;
      }
      const data = await resp.json();
      conversationId = data.conversation_id;
      appendMessage("assistant", data.reply, data.citations);
    } catch (e) {
      typing.remove();
      appendMessage(
        "assistant",
        "I can't reach my server right now. Please email coreymalbright@gmail.com or call (202) 642-6739."
      );
    } finally {
      isSending = false;
      sendBtn.disabled = false;
      input.focus();
    }
  }

  // ------- Public API -----------------------------------------------------

  const api = {
    open: function () {
      if (!panel) return;
      panel.classList.add("victoria-open");
      panel.setAttribute("aria-hidden", "false");
      isOpen = true;
      setTimeout(() => input && input.focus(), 100);
    },
    close: function () {
      if (!panel) return;
      panel.classList.remove("victoria-open");
      panel.setAttribute("aria-hidden", "true");
      isOpen = false;
    },
    toggle: function () {
      isOpen ? api.close() : api.open();
    },
  };
  window.victoria = api;

  // ------- Boot -----------------------------------------------------------

  function boot() {
    injectStyles();
    buildDom();
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
