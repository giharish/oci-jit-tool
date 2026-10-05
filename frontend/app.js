const $ = (id) => document.getElementById(id);
const API_BASE = window.JIT_API_BASE || "/api";
const SESSION_TOKEN_KEY = "JIT_PORTAL_SESSION_TOKEN";

let catalog = { tenancies: [], groups: [], service_allowlist: {}, access_policy: { max_extensions: 2 } };
let state = { requests: [], events: [] };
let session = { email: "anonymous", role: "requester", is_authenticated: false, is_admin: false };
let selectedRequestId = null;
let authPanel = "login";

async function api(path, options = {}) {
  const url = path.startsWith("/api") ? `${API_BASE}${path.slice(4)}` : path;
  const headers = { "content-type": "application/json", ...(options.headers || {}) };
  const token = localStorage.getItem(SESSION_TOKEN_KEY);
  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }
  const response = await fetch(url, { ...options, headers });
  const text = await response.text();
  let data = {};
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = { error: text };
    }
  }
  if (!response.ok) {
    throw new Error(data.error || data.message || `HTTP ${response.status} for ${url}`);
  }
  return data;
}

function setStatus(message) {
  $("status").textContent = message;
}

async function runAction(buttonId, workingMessage, action) {
  const button = buttonId ? $(buttonId) : null;
  const originalText = button?.textContent;
  try {
    if (button) {
      button.disabled = true;
      button.setAttribute("aria-busy", "true");
      button.textContent = "Working...";
    }
    setStatus(workingMessage);
    await action();
  } catch (error) {
    setStatus(error.message);
    alert(error.message);
  } finally {
    if (button) {
      button.textContent = originalText;
      button.disabled = false;
      button.removeAttribute("aria-busy");
    }
    renderSelectedRequest();
  }
}

function formatTimeLeft(expiresAt) {
  if (!expiresAt) return "-";
  const ms = new Date(expiresAt).getTime() - Date.now();
  if (ms <= 0) return "Expired";
  const totalSeconds = Math.floor(ms / 1000);
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = String(totalSeconds % 60).padStart(2, "0");
  if (hours > 0) return `${hours}h ${minutes}m ${seconds}s`;
  return `${minutes}m ${seconds}s`;
}

function minutesLeft(expiresAt) {
  if (!expiresAt) return null;
  return Math.floor((new Date(expiresAt).getTime() - Date.now()) / 60000);
}

function selectedTenancyGroups() {
  const selected = selectedTenancyValues();
  return catalog.groups.filter((group) => selected.includes(group.tenancy_ocid));
}

function selectedTenancyValues() {
  return Array.from(document.querySelectorAll("#targetTenancy input[type='checkbox']:checked")).map((input) => input.value);
}

function setDefaultStartTime() {
  if ($("requestedStartAt").value) return;
  const now = new Date();
  now.setSeconds(0, 0);
  $("requestedStartAt").value = new Date(now.getTime() - now.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
}

function renderSession() {
  $("sessionIdentity").textContent = session.is_authenticated ? session.email : "Anonymous requester";
  $("sessionRole").textContent = session.role;
  $("loginForm").classList.toggle("hidden", session.is_authenticated || authPanel !== "login");
  $("registerForm").classList.toggle("hidden", session.is_authenticated || authPanel !== "register");
  $("verifyForm").classList.toggle("hidden", session.is_authenticated || authPanel !== "verify");
  $("toggleRegisterButton").classList.toggle("hidden", session.is_authenticated);
  $("toggleRegisterButton").textContent = authPanel === "register" ? "Use existing account" : "Create account";
  $("toggleVerifyButton").classList.toggle("hidden", session.is_authenticated);
  $("toggleVerifyButton").textContent = authPanel === "verify" ? "Use existing account" : "Verify account";
  $("logoutButton").classList.toggle("hidden", !session.is_authenticated);
  $("requesterRole").textContent = session.role;
  $("approvalTab").textContent = session.is_admin ? "Approval" : "My Access";
  $("approvalPageEyebrow").textContent = session.is_admin ? "Approval Queue" : "Access Status";
  $("approvalPageTitle").textContent = session.is_admin ? "Approval Queue" : "My Access";
  $("requesterEmail").readOnly = session.is_authenticated;
  if (session.is_authenticated) {
    $("requesterEmail").value = session.email;
    if (session.display_name) {
      $("requesterName").value = session.display_name;
    }
  }
  $("submitRequestButton").disabled = !session.is_authenticated;
  $("requestAuthNotice").classList.toggle("hidden", session.is_authenticated);
  document.querySelectorAll(".admin-only").forEach((element) => element.classList.toggle("hidden", !session.is_admin));
  document.querySelectorAll(".anonymous-extension").forEach((element) => element.classList.toggle("hidden", session.is_authenticated));
  if (!session.is_admin && ["catalogPage", "auditPage"].some((id) => document.getElementById(id).classList.contains("active"))) {
    openTab("requestPage");
  }
}

async function loadSession() {
  const result = await api("/api/auth", { method: "POST", body: JSON.stringify({ action: "session" }) });
  session = result.session || session;
  renderSession();
}

function renderCatalog() {
  $("targetTenancy").innerHTML = catalog.tenancies
    .map(
      (tenancy, index) => `<label class="check-row">
        <input type="checkbox" name="targetTenancy" value="${tenancy.tenancy_ocid}" ${index === 0 ? "checked" : ""} />
        <span><strong>${tenancy.display_name}${tenancy.is_parent ? " (parent)" : ""}</strong><small>${tenancy.tenancy_ocid}</small></span>
      </label>`,
    )
    .join("");
  $("serviceFamily").innerHTML = Object.keys(catalog.service_allowlist)
    .map((name) => `<option value="${name}">${name}</option>`)
    .join("");
  $("tenancyMetric").textContent = catalog.tenancies.length;
  $("groupMetric").textContent = catalog.groups.length;
  $("catalogList").innerHTML = catalog.tenancies
    .map((tenancy) => {
      const groups = catalog.groups.filter((group) => group.tenancy_ocid === tenancy.tenancy_ocid);
      return `<article class="catalog-card">
        <strong>${tenancy.display_name}</strong>
        <small>${tenancy.tenancy_ocid}</small>
        <small>${groups.length} selectable JIT group(s)</small>
      </article>`;
    })
    .join("");
  renderGroups();
  setDefaultStartTime();
}

function renderGroups() {
  const groups = selectedTenancyGroups();
  const uniqueGroups = [...new Map(groups.map((group) => [group.group_name, group])).values()];
  $("groupSelect").innerHTML = groups
    .map((group) => group.group_name)
    .filter((name, index, names) => names.indexOf(name) === index)
    .map((name) => {
      const group = uniqueGroups.find((item) => item.group_name === name);
      return `<option value="${name}">${name} - ${group?.policy_summary || "Terraform-managed policy"}</option>`;
    })
    .join("");
}

function renderRequests() {
  const visibleEmail = session.is_admin ? "all requesters" : session.is_authenticated ? session.email : $("requesterEmail").value.trim();
  $("requestListContext").textContent = session.is_admin
    ? `Showing ${state.requests.length} request(s) across all requesters.`
    : `Showing ${state.requests.length} request(s) for ${visibleEmail || "the requester email entered on the request form"}.`;
  $("requestList").innerHTML = state.requests
    .map((request) => `<button class="request-card ${request.request_id === selectedRequestId ? "active" : ""}" data-request-id="${request.request_id}">
      <strong>#${request.request_id} ${request.access_type.replace("_", " ")}</strong>
      <small class="request-meta"><span>${request.requester_email}</span><span>${request.status}</span></small>
      <small class="request-tenancy">${(request.target_tenancy_ocids || [request.target_tenancy_ocid]).join(", ")}</small>
    </button>`)
    .join("") || `<div class="details muted">No requests yet.</div>`;
  renderSelectedRequest();
}

function selectedRequest() {
  return state.requests.find((request) => request.request_id === selectedRequestId);
}

function membershipSummary(request) {
  const memberships = request.provisioned_memberships || [];
  if (!memberships.length) return "-";
  return memberships
    .map((membership) => {
      const user = membership.user_id || "user pending";
      const group = membership.group_ocid || "group pending";
      return `${membership.tenancy_ocid}: ${user} -> ${group}`;
    })
    .join("; ");
}

function renderSelectedRequest() {
  const request = selectedRequest();
  if (!request) {
    $("selectedTitle").textContent = "No request selected";
    $("selectedStatus").textContent = "none";
    $("selectedDetails").textContent = session.is_admin ? "Select a request to approve, reject, or revoke." : "Select one of your requests to view access status or request an extension.";
    $("approveButton").disabled = true;
    $("rejectButton").disabled = true;
    $("revokeButton").disabled = true;
    $("timerCard").classList.add("hidden");
    $("extensionBox").classList.add("hidden");
    $("warningButton").disabled = true;
    return;
  }
  $("selectedTitle").textContent = `Request #${request.request_id}`;
  $("selectedStatus").textContent = request.status;
  $("selectedDetails").innerHTML = `
    <strong>${request.requester_name}</strong>
    <span>${request.requester_email}</span>
    <span><b>Type:</b> ${request.access_type}</span>
    <span><b>Group:</b> ${request.requested_group_name || request.group_ocid || "-"}</span>
    <span><b>Service:</b> ${request.service_family || "-"}</span>
    <span><b>Verb:</b> ${request.verb || "-"}</span>
    <span><b>Jira:</b> ${request.jira_issue_key || "-"}</span>
    <span><b>Requested start:</b> ${request.requested_start_at || request.created_at || "-"}</span>
    <span><b>Expires:</b> ${request.expires_at || "-"}</span>
    <span><b>Tenancies:</b> ${(request.target_tenancy_ocids || [request.target_tenancy_ocid]).length}</span>
    <span><b>Provisioned memberships:</b> ${membershipSummary(request)}</span>
    <span><b>Extensions used:</b> ${request.extension_count || 0}/${catalog.access_policy.max_extensions || 2}</span>
    <span><b>Extension status:</b> ${request.extension_status || "-"}</span>`;
  const isAuthority = session.is_admin;
  const isRequester = session.email === request.requester_email || (!session.is_authenticated && $("requesterEmail").value.trim() === request.requester_email);
  $("approveButton").disabled = !isAuthority || request.status !== "requested";
  $("rejectButton").disabled = !isAuthority || request.status !== "requested";
  $("revokeButton").disabled = !isAuthority || request.status !== "active";
  $("warningButton").disabled = !isAuthority;
  $("timerCard").classList.toggle("hidden", request.status !== "active");
  $("extensionBox").classList.toggle("hidden", request.status !== "active");
  $("extensionMinutes").max = request.duration_minutes;
  $("extensionMinutes").value = Math.min(Number($("extensionMinutes").value || request.duration_minutes), request.duration_minutes);
  $("requestExtensionButton").disabled =
    !isRequester || request.status !== "active" || request.extension_status === "extension_pending" || (request.extension_count || 0) >= (catalog.access_policy.max_extensions || 2);
  $("approveExtensionButton").disabled = !isAuthority || request.extension_status !== "extension_pending";
  $("rejectExtensionButton").disabled = !isAuthority || request.extension_status !== "extension_pending";
  updateTimer();
}

function updateTimer() {
  const request = selectedRequest();
  if (!request || request.status !== "active") return;
  const left = minutesLeft(request.expires_at);
  $("timeLeft").textContent = formatTimeLeft(request.expires_at);
  $("timerCard").classList.toggle("warning", left !== null && left < 15);
  const remainingExtensions = Math.max(0, (catalog.access_policy.max_extensions || 2) - (request.extension_count || 0));
  $("extensionInfo").textContent = `${remainingExtensions} extension(s) remaining. Max per extension: ${request.duration_minutes} minute(s).`;
}

function renderAudit() {
  $("auditLog").innerHTML = state.events
    .map((event) => `<li>
      <time>${event.created_at} · ${event.action} · ${event.result}</time>
      <span>${event.actor || "system"} ${event.target_tenancy_ocid || ""}</span>
    </li>`)
    .join("") || `<li><span>No audit events yet.</span></li>`;
}

async function loadCatalog() {
  catalog = await api("/api/catalog");
  if (!catalog.tenancies.length) {
    setStatus("Catalog is empty. Click Sync Catalog to discover tenancies and groups.");
  }
  renderSession();
  renderCatalog();
}

async function loadRequests() {
  state = await api("/api/requests", {
    method: "POST",
    body: JSON.stringify({ action: "list" }),
  });
  session = state.session || session;
  renderSession();
  if (state.requests.length && !state.requests.some((request) => request.request_id === selectedRequestId)) {
    selectedRequestId = state.requests[0].request_id;
  }
  if (!state.requests.length) {
    selectedRequestId = null;
  }
  renderRequests();
  renderAudit();
}

function collectRequestPayload() {
  const accessType = $("accessType").value;
  const targetTenancies = selectedTenancyValues();
  const groupName = $("groupSelect").value;
  const startValue = $("requestedStartAt").value;
  return {
    requester_name: $("requesterName").value.trim(),
    requester_email: $("requesterEmail").value.trim(),
    target_tenancy_ocid: targetTenancies[0],
    target_tenancy_ocids: targetTenancies,
    requested_start_at: startValue ? new Date(startValue).toISOString() : undefined,
    target_compartment_ocid: $("compartmentOcid").value.trim(),
    access_type: accessType,
    requested_group_name: accessType === "standard_group" ? groupName : $("customGroupName").value.trim(),
    service_family: accessType === "custom_policy" ? $("serviceFamily").value : undefined,
    verb: accessType === "custom_policy" ? $("verb").value : undefined,
    scope_type: accessType === "custom_policy" ? $("scopeType").value : undefined,
    duration_minutes: Number($("duration").value),
    justification: $("justification").value.trim(),
  };
}

document.addEventListener("click", async (event) => {
  const tab = event.target.closest("[data-tab]");
  if (tab) {
    openTab(tab.dataset.tab);
    return;
  }
  const card = event.target.closest("[data-request-id]");
  if (card) {
    selectedRequestId = Number(card.dataset.requestId);
    renderRequests();
  }
});

function openTab(tabId) {
  document.querySelectorAll(".tab").forEach((button) => button.classList.toggle("active", button.dataset.tab === tabId));
  document.querySelectorAll(".page").forEach((page) => page.classList.toggle("active", page.id === tabId));
}

$("targetTenancy").addEventListener("change", renderGroups);
$("accessType").addEventListener("change", () => {
  const custom = $("accessType").value === "custom_policy";
  $("customFields").classList.toggle("hidden", !custom);
  document.querySelectorAll(".standard-only").forEach((item) => item.classList.toggle("hidden", custom));
});

$("syncCatalogButton").addEventListener("click", async () => {
  await runAction("syncCatalogButton", "Syncing catalog from OCI...", async () => {
    const result = await api("/api/catalog", { method: "POST", body: JSON.stringify({ action: "sync" }) });
    await loadCatalog();
    setStatus(`Synced ${result.tenancies} tenancies and ${result.groups} JIT groups.`);
  });
});

$("previewPolicyButton").addEventListener("click", async () => {
  try {
    const result = await api("/api/policy/preview", { method: "POST", body: JSON.stringify(collectRequestPayload()) });
    $("policyPreview").textContent = result.statements.join("\n");
  } catch (error) {
    $("policyPreview").textContent = error.message;
  }
});

$("requestForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  await runAction("submitRequestButton", "Submitting access request...", async () => {
    const created = await api("/api/requests", { method: "POST", body: JSON.stringify(collectRequestPayload()) });
    selectedRequestId = created.request_id;
    await loadRequests();
    setStatus(`Request #${created.request_id} created and sent for approval.`);
  });
});

$("refreshRequestsButton").addEventListener("click", async () => {
  await runAction("refreshRequestsButton", "Refreshing requests...", async () => {
    await loadRequests();
    setStatus("Requests refreshed.");
  });
});
$("approveButton").addEventListener("click", async () => {
  const requestId = selectedRequestId;
  await runAction("approveButton", `Approving request #${requestId}...`, async () => {
    const result = await api("/api/approval", { method: "POST", body: JSON.stringify({ request_id: requestId, decision: "approved" }) });
    await loadRequests();
    setStatus(result.status === "scheduled" ? `Request #${requestId} approved and scheduled for its start time.` : `Request #${requestId} approved and provisioned.`);
  });
});
$("rejectButton").addEventListener("click", async () => {
  const requestId = selectedRequestId;
  await runAction("rejectButton", `Rejecting request #${requestId}...`, async () => {
    await api("/api/approval", { method: "POST", body: JSON.stringify({ request_id: requestId, decision: "rejected" }) });
    await loadRequests();
    setStatus(`Request #${requestId} rejected.`);
  });
});
$("revokeButton").addEventListener("click", async () => {
  const requestId = selectedRequestId;
  await runAction("revokeButton", `Revoking request #${requestId}...`, async () => {
    await api("/api/revoke", { method: "POST", body: JSON.stringify({ request_id: requestId, reason: "manual" }) });
    await loadRequests();
    setStatus(`Request #${requestId} revoked.`);
  });
});
$("requestExtensionButton").addEventListener("click", async () => {
  const requestId = selectedRequestId;
  await runAction("requestExtensionButton", `Requesting extension for request #${requestId}...`, async () => {
    const minutes = Number($("extensionMinutes").value);
    await api("/api/extension/request", {
      method: "POST",
      body: JSON.stringify({ request_id: requestId, extension_minutes: minutes, extension_token: $("extensionToken").value.trim() }),
    });
    await loadRequests();
    setStatus(`Extension requested for request #${requestId}.`);
  });
});
$("approveExtensionButton").addEventListener("click", async () => {
  const requestId = selectedRequestId;
  await runAction("approveExtensionButton", `Approving extension for request #${requestId}...`, async () => {
    await api("/api/extension/approve", {
      method: "POST",
      body: JSON.stringify({ request_id: requestId, decision: "approved" }),
    });
    await loadRequests();
    setStatus(`Extension approved. Timer reset for request #${requestId}.`);
  });
});
$("rejectExtensionButton").addEventListener("click", async () => {
  const requestId = selectedRequestId;
  await runAction("rejectExtensionButton", `Rejecting extension for request #${requestId}...`, async () => {
    await api("/api/extension/approve", {
      method: "POST",
      body: JSON.stringify({ request_id: requestId, decision: "rejected" }),
    });
    await loadRequests();
    setStatus(`Extension rejected for request #${requestId}.`);
  });
});
$("warningButton").addEventListener("click", async () => {
  await runAction("warningButton", "Running warning worker...", async () => {
    const result = await api("/api/notifications/warnings", { method: "POST", body: "{}" });
    await loadRequests();
    setStatus(`Warning worker sent ${result.warnings.length} expiry email(s).`);
  });
});
$("expiryButton").addEventListener("click", async () => {
  await runAction("expiryButton", "Running expiry worker...", async () => {
    const result = await api("/api/expiry/run", { method: "POST", body: "{}" });
    await loadRequests();
    setStatus(`Expiry worker activated ${(result.activated || []).length} request(s), sent ${result.warnings.length} warning(s), and revoked ${result.revoked.length} request(s).`);
  });
});
$("loginForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  await runAction("loginButton", "Signing in...", async () => {
    const result = await api("/api/auth", {
      method: "POST",
      body: JSON.stringify({ action: "login", email: $("loginEmail").value.trim(), password: $("loginPassword").value }),
    });
    localStorage.setItem(SESSION_TOKEN_KEY, result.token);
    session = result.session || session;
    $("loginPassword").value = "";
    renderSession();
    await loadRequests();
    setStatus(`Signed in as ${session.email}.`);
  });
});
$("toggleRegisterButton").addEventListener("click", () => {
  authPanel = authPanel === "register" ? "login" : "register";
  renderSession();
});
$("toggleVerifyButton").addEventListener("click", () => {
  authPanel = authPanel === "verify" ? "login" : "verify";
  renderSession();
});
$("registerForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  await runAction("registerButton", "Creating account...", async () => {
    const result = await api("/api/auth", {
      method: "POST",
      body: JSON.stringify({
        action: "register",
        display_name: $("registerName").value.trim(),
        email: $("registerEmail").value.trim(),
        password: $("registerPassword").value,
      }),
    });
    if (result.verification_required) {
      $("verifyEmail").value = result.email || $("registerEmail").value.trim();
      authPanel = "verify";
      $("registerPassword").value = "";
      renderSession();
      setStatus(result.message || "Verification email sent. Enter the token to activate your account.");
      return;
    }
    localStorage.setItem(SESSION_TOKEN_KEY, result.token);
    session = result.session || session;
    authPanel = "login";
    $("registerPassword").value = "";
    renderSession();
    await loadRequests();
    setStatus(`Account created and signed in as ${session.email}.`);
  });
});
$("verifyForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  await runAction("verifyButton", "Verifying account...", async () => {
    const result = await api("/api/auth", {
      method: "POST",
      body: JSON.stringify({ action: "verify", email: $("verifyEmail").value.trim(), token: $("verifyToken").value.trim() }),
    });
    localStorage.setItem(SESSION_TOKEN_KEY, result.token);
    session = result.session || session;
    $("verifyToken").value = "";
    authPanel = "login";
    renderSession();
    await loadRequests();
    setStatus(`Account verified and signed in as ${session.email}.`);
  });
});
$("logoutButton").addEventListener("click", async () => {
  await runAction("logoutButton", "Signing out...", async () => {
    localStorage.removeItem(SESSION_TOKEN_KEY);
    selectedRequestId = null;
    state = { requests: [], events: [] };
    await loadSession();
    await loadRequests();
    setStatus("Signed out. Sign in to create or monitor access requests.");
  });
});

window.setInterval(updateTimer, 1000);

const query = new URLSearchParams(window.location.search);
if (query.get("verify_email") || query.get("verification_token")) {
  authPanel = "verify";
  $("verifyEmail").value = query.get("verify_email") || "";
  $("verifyToken").value = query.get("verification_token") || "";
}

loadSession().then(loadCatalog).then(loadRequests).catch((error) => {
  document.body.innerHTML = `<main class="shell"><section class="panel"><h1>App failed to load</h1><p>${error.message}</p></section></main>`;
});
