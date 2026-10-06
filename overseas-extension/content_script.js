(() => {
  const EXTENSION_BUILD = "batch25d2-runtime-20260817-r38";
  const EXTENSION_PROTOCOL = 1;
  const RECRUITER_DOM_CONTRACT_VERSION = "batch25d2.recruiter.search.dom.v2";
  const VERSION = EXTENSION_PROTOCOL;
  const PUBLIC_PROFILE = /^\/in\/[^/?#]+\/?$/;
  const PUBLIC_PROFILE_DETAIL = /^\/in\/[^/?#]+\/details\/(skills|languages)\/?$/;
  const PUBLIC_CONTACT_INFO = /^\/in\/[^/?#]+\/overlay\/contact-info\/?$/;
  const PEOPLE_SEARCH = /^\/search\/results\/people\/?$/;
  const RECRUITER_SEARCH = /^\/talent\/search(?:\/people)?\/?$/;
  const RECRUITER_PROFILE = /^\/talent\/(?:search\/)?profile\/[^/?#]+\/?$/;
  const RECRUITER_RESULT_PROFILE = /^\/talent\/profile\/[^/?#]+\/?$/;
  const RECRUITER_PRIVATE_PROFILE_ROUTE = /^\/talent\/(?:search\/)?profile\/[^/?#]+(?:\/.*)?$/;
  const RECRUITER_PROFILE_ENDPOINT = "/talent/api/talentLinkedInMemberProfiles/";
  const PROFILE_DETAIL_KINDS = new Set(["skills", "languages"]);
  const PRIVATE_ERROR_CATEGORIES = new Set([
    "network", "disconnected", "auth_required", "challenge_detected", "permission_denied",
    "rate_limited", "account_changed", "page_changed", "contract_mismatch",
  ]);
  const READY_RETRY_DELAYS_MS = Object.freeze([500, 1_500, 5_000, 15_000, 30_000]);
  const RECRUITER_FILTERS = Object.freeze({
    title: Object.freeze({
      buttonAria: "添加职位头衔或布尔搜索条件",
      ariaLabels: Object.freeze(["职位头衔"]),
      placeholders: Object.freeze(["输入职位头衔或布尔条件…", "输入职位头衔…"]),
    }),
    location: Object.freeze({ buttonAria: "添加候选人所在地点", placeholder: "输入地区…" }),
    current_company: Object.freeze({
      buttonText: "公司或布尔搜索条件",
      ariaLabels: Object.freeze(["公司"]),
      placeholder: "输入公司或布尔条件…",
    }),
    keywords: Object.freeze({ buttonAria: "添加候选人档案关键词或布尔条件", placeholder: "输入关键词…", freeText: true }),
    profile_language: Object.freeze({
      buttonAria: "语言能力",
      ariaLabels: Object.freeze(["语言", "口语"]),
      placeholders: Object.freeze(["输入语言…", "输入口语…"]),
    }),
    years_of_experience: Object.freeze({
      buttonAria: "经验年限",
      buttonText: "经验年限",
      range: true,
    }),
  });
  const RECRUITER_UNAVAILABLE_FILTERS = new Set();
  const RECRUITER_RECOVERY_FILTERS = new Set(["title", "location", "current_company", "keywords"]);
  const RECRUITER_RECOVERY_INTENTS = new Set(["select_filter_value", "submit_search"]);
  const RECRUITER_RECOVERY_ACTIONS = new Set([
    "click", "focus", "type", "press_enter", "press_arrow_down_enter", "noop",
  ]);
  const RECRUITER_CANDIDATE_TTL_MS = 30_000;
  const SECTION_ALIASES = Object.freeze({
    about: new Set(["about", "关于"]),
    experience: new Set(["experience", "work experience", "工作经历", "经历"]),
    education: new Set(["education", "教育经历", "教育"]),
    skills: new Set(["skills", "skill", "技能"]),
    languages: new Set(["languages", "language", "语言"]),
    certifications: new Set(["licenses & certifications", "certifications", "资质认证", "证书"]),
    projects: new Set(["projects", "project", "项目", "项目经历", "所做项目"]),
  });
  const PRIVATE_CONTRACTS = Object.freeze({
    recruiter: Object.freeze({
      enabled: true,
      version: "batch25d2.recruiter.profile.v1",
      headerNames: Object.freeze(["csrf-token", "x-li-track", "x-restli-protocol-version"]),
      routes: Object.freeze({
        search: Object.freeze([RECRUITER_SEARCH]),
        profile: Object.freeze([RECRUITER_PROFILE, RECRUITER_RESULT_PROFILE]),
      }),
      operations: Object.freeze({
        profile_detail_full: Object.freeze({
          kind: "profile",
          method: "POST",
          allowCsrf: true,
          project: projectObservedRecruiterProfile,
        }),
      }),
    }),
    recruiter_lite: Object.freeze({
      enabled: false,
      version: "recruiter-lite-disabled",
      headerNames: Object.freeze([]),
      routes: Object.freeze({ search: Object.freeze([]), profile: Object.freeze([]) }),
      operations: Object.freeze({}),
    }),
  });
  const RECRUITER_PROFILE_OBSERVED = Object.freeze({
    contractVersion: "batch25d.recruiter.profile.observed.v1",
    routePattern: "/talent/search/profile/<opaque_ref>",
    endpointPath: "/talent/api/talentLinkedInMemberProfiles/<opaque_ref>",
    experiencePath: "groupedWorkExperience[].positions[]",
    operations: Object.freeze([
      Object.freeze({
        name: "profile_detail_full",
        method: "POST",
        queryKeys: Object.freeze([]),
        bodyKeys: Object.freeze(["altkey", "decoration"]),
        typedProfileEligible: true,
      }),
      Object.freeze({
        name: "profile_detail_summary",
        method: "GET",
        queryKeys: Object.freeze(["altkey", "decoration"]),
        bodyKeys: Object.freeze([]),
        typedProfileEligible: false,
      }),
    ]),
    requiredHeaderNames: Object.freeze([]),
    fixedProtocolHeaderNames: Object.freeze([
      "content-type",
      "x-http-method-override",
    ]),
    differentiallyRequiredHeaderNames: Object.freeze([
      "x-li-track",
      "x-restli-protocol-version",
    ]),
    observedSensitiveSessionHeaderNames: Object.freeze(["csrf-token"]),
    productionEnabled: true,
  });
  const FORBIDDEN_HEADERS = /^(?:cookie|authorization|proxy-authorization|set-cookie|csrf-token|x-li-identity|origin|referer|user-agent|sec-)/i;
  const ROLE_ACTIONS = {
    sourcing: new Set(["open_search", "apply_filters", "next_page", "collect_batch"]),
    profile_analyst: new Set([
      "open_profile", "extract_profile", "open_contact_info", "extract_contact_info", "import_candidates",
    ]),
  };
  const FILTER_KEYS = new Set([
    "keywords", "location", "current_company", "past_company", "industry",
    "school", "title", "profile_language", "network", "skills",
  ]);
  let monitoredRunId = null;
  let monitorTimer = null;
  let navigationWatcherTimer = null;
  let lastObservedNavigationHref = location.href;
  let activeFetchController = null;
  let lastHref = location.href;
  let lastAccountHint = "";
  let pendingAccountHint = "";
  let pendingAccountSamples = 0;
  // A worker may resend the same command after its message timeout even
  // though the page is still carrying out the original DOM operation. Keep
  // only the in-flight Promise so a duplicate command_id cannot click twice;
  // deleting it after settlement preserves an explicit later retry.
  const inFlightCommandTasks = new Map();
  const recruiterCandidateSnapshots = new Map();
  let recruiterCandidateSnapshotSequence = 0;

  function commandTaskKey(message) {
    return JSON.stringify([message.run_id, message.step_id, message.command_id, message.lease_id || ""]);
  }

  function runCommandOnce(message, operation) {
    const key = commandTaskKey(message);
    const existing = inFlightCommandTasks.get(key);
    if (existing) return existing;
    const task = Promise.resolve().then(operation).catch((caught) => ({
      status: caught.pauseReason ? "paused" : "failed",
      data: {},
      pause_reason: caught.pauseReason,
      error: caught.message || "页面动作失败",
      ...(Array.isArray(caught.contractDiagnostics)
        ? { diagnostics: caught.contractDiagnostics.slice(0, 50) }
        : {}),
    }));
    inFlightCommandTasks.set(key, task);
    task.then(() => {
      if (inFlightCommandTasks.get(key) === task) inFlightCommandTasks.delete(key);
    }, () => {
      if (inFlightCommandTasks.get(key) === task) inFlightCommandTasks.delete(key);
    });
    return task;
  }

  function pageKind() {
    if (location.hostname !== "www.linkedin.com") return "unsupported";
    if (PEOPLE_SEARCH.test(location.pathname)) return "people_search";
    if (PUBLIC_PROFILE.test(location.pathname)) return "public_profile";
    if (PUBLIC_PROFILE_DETAIL.test(location.pathname)) return "public_profile_detail";
    if (PUBLIC_CONTACT_INFO.test(location.pathname)) return "public_contact_info";
    for (const [surface, contract] of Object.entries(PRIVATE_CONTRACTS)) {
      for (const kind of ["search", "profile"]) {
        if (contract.enabled && contract.routes[kind].some((route) => route.test(location.pathname))) {
          return `${surface}_${kind}`;
        }
      }
    }
    return "unsupported";
  }

  function canonicalNavigationUrl(value) {
    try {
      const url = new URL(value);
      url.pathname = url.pathname.replace(/\/+$/, "") || "/";
      url.hash = "";
      // Search keeps its meaningful query; profile/contact identity is the
      // pathname only while LinkedIn settles benign query state.
      if (PEOPLE_SEARCH.test(url.pathname)) url.searchParams.delete("origin");
      if (PUBLIC_PROFILE.test(url.pathname) || PUBLIC_PROFILE_DETAIL.test(url.pathname) || PUBLIC_CONTACT_INFO.test(url.pathname) || RECRUITER_PRIVATE_PROFILE_ROUTE.test(url.pathname)) url.search = "";
      url.search = new URLSearchParams(
        [...url.searchParams.entries()].sort(([leftKey, leftValue], [rightKey, rightValue]) =>
          leftKey.localeCompare(rightKey) || leftValue.localeCompare(rightValue)
        ),
      ).toString();
      return url.toString();
    } catch (_error) {
      return "";
    }
  }

  function reportedUrl(value) {
    try {
      const url = new URL(value);
      if (RECRUITER_PRIVATE_PROFILE_ROUTE.test(url.pathname)) return `${url.origin}/talent/search/profile/redacted`;
      if (RECRUITER_SEARCH.test(url.pathname)) return `${url.origin}/talent/search`;
      return value.slice(0, 2048);
    } catch (_error) {
      return "";
    }
  }

  function stopMonitor() {
    clearInterval(monitorTimer);
    monitorTimer = null;
    monitoredRunId = null;
    activeFetchController?.abort();
    activeFetchController = null;
  }

  function reportPageChanged(previousUrl, url, challenge = false, accountChanged = false) {
    // Snapshot the run/page state before yielding. stopMonitor() can clear the
    // mutable globals in the same turn, and a late message with run_id:null
    // would bypass the service worker's active-run pause guard.
    const runId = monitoredRunId;
    const payload = {
      type: "AGENT_PAGE_CHANGED",
      version: VERSION,
      run_id: runId,
      previous_url: reportedUrl(previousUrl),
      url: reportedUrl(url),
      page_kind: pageKind(),
      challenge,
      account_changed: accountChanged,
    };
    Promise.resolve().then(() => chrome.runtime.sendMessage(payload)).catch(() => {});
  }

  function observeNavigationChange() {
    const currentHref = location.href;
    const currentCanonical = canonicalNavigationUrl(currentHref);
    const previousHref = lastObservedNavigationHref;
    if (!currentCanonical || currentCanonical === canonicalNavigationUrl(previousHref)) {
      if (!readySent && readyRetryTimer === null) signalReady();
      return;
    }
    lastObservedNavigationHref = currentHref;
    resetReadyState();
    // The run monitor owns active-run pause semantics (including challenge and
    // account binding checks). In idle mode, still report the route so the
    // background can refresh its durable page/session state.
    if (!monitoredRunId) reportPageChanged(previousHref, currentHref);
    Promise.resolve().then(() => signalReady()).catch(() => {});
  }

  function startNavigationWatcher() {
    if (navigationWatcherTimer !== null) return;
    lastObservedNavigationHref = location.href;
    for (const method of ["pushState", "replaceState"]) {
      try {
        const original = history[method];
        if (typeof original !== "function") continue;
        history[method] = function (...args) {
          const result = original.apply(this, args);
          Promise.resolve().then(observeNavigationChange).catch(() => {});
          return result;
        };
      } catch (_error) {
        // The bounded polling fallback below still covers non-configurable
        // history methods and page scripts running in another JS world.
      }
    }
    addEventListener("popstate", observeNavigationChange);
    navigationWatcherTimer = setInterval(observeNavigationChange, 1000);
  }

  function startMonitor(runId) {
    if (typeof runId !== "string" || !runId || runId.length > 200) throw new Error("任务 ID 无效");
    stopMonitor();
    monitoredRunId = runId;
    lastHref = location.href;
    lastAccountHint = accountHint();
    pendingAccountHint = "";
    pendingAccountSamples = 0;
    monitorTimer = setInterval(() => {
      const challenge = challengeDetected();
      const currentAccountHint = accountHint();
      let accountChanged = false;
      if (!currentAccountHint || !lastAccountHint || currentAccountHint === lastAccountHint) {
        if (currentAccountHint && !lastAccountHint) lastAccountHint = currentAccountHint;
        pendingAccountHint = "";
        pendingAccountSamples = 0;
      } else {
        pendingAccountSamples = currentAccountHint === pendingAccountHint
          ? pendingAccountSamples + 1
          : 1;
        pendingAccountHint = currentAccountHint;
        accountChanged = pendingAccountSamples >= 3;
      }
      if (canonicalNavigationUrl(location.href) === canonicalNavigationUrl(lastHref) && !challenge && !accountChanged) {
        lastHref = location.href;
        return;
      }
      activeFetchController?.abort();
      const changedFrom = lastHref;
      lastHref = location.href;
      clearInterval(monitorTimer);
      monitorTimer = null;
      lastObservedNavigationHref = location.href;
      reportPageChanged(changedFrom, location.href, challenge, accountChanged);
    }, 500);
  }

  function challengeDetected() {
    if (/\/(checkpoint|authwall|challenge)(?:\/|$)/i.test(location.pathname)) return true;
    const signal = document.querySelector(
      "form[action*='checkpoint'],iframe[src*='captcha'],[id*='captcha'],[name*='captcha']," +
      "[data-test-id*='challenge'],[role='alert'][data-test-id*='checkpoint']"
    );
    return Boolean(signal);
  }

  function cleanPublicUrl(value, expectedKind = "public_profile") {
    try {
      const url = new URL(value, location.origin);
      if (url.protocol !== "https:" || url.hostname !== "www.linkedin.com") return null;
      const matches = expectedKind === "people_search"
        ? PEOPLE_SEARCH.test(url.pathname)
        : PUBLIC_PROFILE.test(url.pathname);
      if (!matches) return null;
      return `${url.origin}${url.pathname.replace(/\/+$/, "")}`;
    } catch (_error) {
      return null;
    }
  }

  function publicIdentity(value) {
    try {
      const url = new URL(value, location.origin);
      if (url.protocol !== "https:" || url.hostname !== "www.linkedin.com" || url.port || url.username || url.password) return null;
      const match = url.pathname.match(/^\/in\/([^/?#]+)(?:(?:\/overlay\/contact-info)|(?:\/details\/(?:skills|languages)))?\/?$/);
      if (!match) return null;
      const vanity = decodeURIComponent(match[1]).normalize("NFKC").trim().replace(/^@+/, "");
      return /^[A-Za-z0-9._-]{1,100}$/.test(vanity)
        ? `${url.origin}/in/${vanity.toLowerCase()}`
        : null;
    } catch (_error) {
      return null;
    }
  }

  function cleanContactUrl(value) {
    try {
      const url = new URL(value, location.origin);
      if (url.protocol !== "https:" || url.hostname !== "www.linkedin.com" || url.port || url.username || url.password ||
          !PUBLIC_CONTACT_INFO.test(url.pathname) || publicIdentity(url) !== publicIdentity(location.href)) return null;
      return `${url.origin}${url.pathname.replace(/\/+$/, "")}/`;
    } catch (_error) {
      return null;
    }
  }

  function cleanProfileDetailUrl(value, expectedProfileUrl) {
    try {
      const url = new URL(value, location.origin);
      const match = url.pathname.match(/^\/in\/([^/?#]+)\/details\/(skills|languages)\/?$/);
      if (url.protocol !== "https:" || url.hostname !== "www.linkedin.com" || url.port ||
          url.username || url.password || url.search || url.hash || !match ||
          !PROFILE_DETAIL_KINDS.has(match[2]) ||
          publicIdentity(url) !== publicIdentity(expectedProfileUrl)) return null;
      return {
        kind: match[2],
        url: `${url.origin}${url.pathname.replace(/\/+$/, "")}/`,
      };
    } catch (_error) {
      return null;
    }
  }

  function visibleElement(node, rejectClipping = false) {
    if (!node || node.hidden || node.closest("[hidden],[aria-hidden='true'],[inert]")) return false;
    for (let current = node; current; current = current.parentElement) {
      const style = getComputedStyle(current);
      const rect = current.getBoundingClientRect();
      if (style.display === "none" || ["hidden", "collapse"].includes(style.visibility) ||
          style.contentVisibility === "hidden" || Number(style.opacity) === 0 ||
          (rejectClipping && style.clipPath !== "none") ||
          (["hidden", "clip", "scroll", "auto"].includes(style.overflowX) && rect.width <= 0) ||
          (["hidden", "clip", "scroll", "auto"].includes(style.overflowY) && rect.height <= 0)) return false;
    }
    return [...node.getClientRects()].some((rect) => rect.width > 0 && rect.height > 0);
  }

  function recruiterLocale() {
    const language = (document.documentElement.lang || "").toLowerCase().replaceAll("_", "-");
    if (language !== "zh" && !language.startsWith("zh-")) throw new Error("Recruiter locale contract mismatch");
  }


  function oneVisible(nodes, label) {
    const visible = [...nodes].filter((node) => visibleElement(node));
    if (visible.length !== 1) throw new Error(`Recruiter ${label} DOM contract mismatch`);
    return visible[0];
  }

  async function oneVisibleAfterRetry(factory, label, attempts = 2) {
    for (let attempt = 0; attempt < attempts; attempt += 1) {
      try { return oneVisible(factory(), label); } catch (error) {
        if (attempt === attempts - 1) throw error;
        await wait(400);
      }
    }
  }

  async function recruiterGlobalSearch() {
    recruiterLocale();
    const fields = () => [...new Set([
      ...document.querySelectorAll(
        "input[role='combobox'][aria-label='搜索职位头衔、目标人才、关键词或布尔条件等'][placeholder='在此输入任意内容']," +
        "input[role='combobox'][aria-label='关闭搜索输入'][placeholder='在此输入任意内容']," +
        "[data-test-global-nav-copilot-typeahead] [data-test-copilot-typeahead-typeahead-input] input[role='combobox']"
      ),
      // Current Recruiter renders the global field without aria/placeholder;
      // its fixed copilot container is the only stable scope for that input.
      ...document.querySelectorAll(
        "[data-test-global-nav-copilot-typeahead] #copilot-typeahead-typeahead-input input[role='combobox']," +
        "#copilot-typeahead-form #copilot-typeahead-typeahead-input input[role='combobox']"
      ),
      // Recruiter currently exposes the active Copilot control as a textarea;
      // the nested typeahead input remains hidden for autocomplete only.
      ...document.querySelectorAll(
        "[data-test-global-nav-copilot-typeahead] textarea[data-test-copilot-chat-input][placeholder='在此输入任何内容即可开始']," +
        "#copilot-typeahead-form textarea[data-test-copilot-chat-input][placeholder='在此输入任何内容即可开始']"
      ),
    ])];
    const toggles = () => new Set([
      ...document.querySelectorAll("button[data-test-global-nav-search-button]"),
      ...document.querySelectorAll("button[aria-label='打开搜索输入']"),
    ]);
    const filterOnlyToggles = () => [...document.querySelectorAll("button,[role='button']")].filter((node) =>
      visibleElement(node) && (node.textContent || "").normalize("NFKC").trim() === "显示筛选条件"
    );
    let toggleClicked = false;
    // Recruiter mounts the global control after the page script is ready.
    // Keep a bounded wait for slow hydration so it does not become a false
    // contract failure, while still failing closed on ambiguous DOM.
    for (let attempt = 0; attempt < 30; attempt += 1) {
      const visible = [...fields()].filter((node) => visibleElement(node));
      if (visible.length === 1) return visible[0];
      if (visible.length > 1) throw new Error("Recruiter global search DOM contract mismatch");
      const visibleToggles = [...toggles()].filter((node) => visibleElement(node));
      if (visibleToggles.length > 1) throw new Error("Recruiter global search toggle DOM contract mismatch");
      if (visibleToggles.length === 1 && !toggleClicked) {
        visibleToggles[0].click();
        toggleClicked = true;
      }
      if (attempt < 29) await wait(400);
    }
    if (filterOnlyToggles().length > 1) throw new Error("Recruiter filter panel DOM contract mismatch");
    throw new Error("Recruiter global search DOM contract mismatch");
  }

  function recruiterResultRoot({ allowTransient = false } = {}) {
    const roots = [...document.querySelectorAll("main,[role='main']")]
      .filter((node) => visibleElement(node));
    if (roots.length === 1) return roots[0];
    const leafRoots = roots.filter((root) =>
      !roots.some((other) => other !== root && root.contains(other))
    );
    const hasResultSemantics = (root) =>
      [...root.querySelectorAll("a[href]")].some((node) => {
        if (!visibleElement(node, true)) return false;
        try {
          const target = new URL(node.getAttribute("href") || "", location.origin);
          return target.origin === location.origin && !target.hash && RECRUITER_RESULT_PROFILE.test(target.pathname);
        } catch (_error) {
          return false;
        }
      }) || resultCount(root) !== null || recruiterSearchHasExplicitEmptyState(root);
    const semanticLeaves = leafRoots.filter(hasResultSemantics);
    if (semanticLeaves.length === 1) return semanticLeaves[0];
    const semanticRoots = roots.filter(hasResultSemantics);
    if (semanticRoots.length === 1) return semanticRoots[0];
    if (allowTransient) return null;
    throw new Error("Recruiter search results DOM contract mismatch");
  }

  function recruiterSearchResultState({ allowTransient = false } = {}) {
    const root = recruiterResultRoot({ allowTransient });
    if (!root) return { ready: false, signature: "" };
    const profiles = [...root.querySelectorAll("a[href]")]
      .filter((node) => visibleElement(node, true))
      .map((node) => {
        try {
          const target = new URL(node.getAttribute("href") || "", location.origin);
          return target.origin === location.origin && !target.hash && RECRUITER_RESULT_PROFILE.test(target.pathname)
            ? target.pathname
            : "";
        } catch (_error) {
          return "";
        }
      })
      .filter(Boolean)
      .slice(0, 25);
    const count = resultCount(root);
    const empty = recruiterSearchHasExplicitEmptyState(root);
    return {
      ready: profiles.length > 0 || count !== null || empty,
      signature: JSON.stringify({ profiles, count, empty }),
    };
  }

  async function recruiterBooleanSearchAction(control, attempts = 30) {
    let sawControlledPopup = false;
    for (let attempt = 0; attempt < attempts; attempt += 1) {
      const controlledIds = String(control?.getAttribute("aria-controls") || "")
        .split(/\s+/)
        .filter(Boolean);
      if (controlledIds.length) {
        sawControlledPopup = true;
        const roots = [...new Set(controlledIds.map((id) => document.getElementById(id)).filter(Boolean))];
        const visibleRoots = roots.filter((root) => visibleElement(root));
        if (visibleRoots.length > 1 ||
            (visibleRoots.length === 1 && visibleRoots[0].getAttribute("role") !== "dialog")) {
          throw new Error("Recruiter global search action DOM contract mismatch");
        }
        if (visibleRoots.length === 1) {
          const root = visibleRoots[0];
          const candidates = [...new Set(
            [...root.querySelectorAll("[data-test-hire-better-ta-entry-boolean-search]")]
              .map((node) => node.closest("li[role='link']"))
              .filter((node) => node && root.contains(node) && visibleElement(node))
          )];
          if (candidates.length > 1) {
            throw new Error("Recruiter global search action DOM contract mismatch");
          }
          const query = recruiterText(control.value || "");
          if (candidates.length === 1 && query &&
              recruiterText(candidates[0].textContent || "").includes(query)) {
            return candidates[0];
          }
        }
      } else if (!sawControlledPopup && attempt >= 9) {
        return null;
      }
      if (attempt < attempts - 1) await wait(100);
    }
    if (sawControlledPopup) {
      throw new Error("Recruiter global search action DOM contract mismatch");
    }
    return null;
  }

  function recruiterSearchQueryApplied(control, query) {
    const expected = recruiterText(query || "");
    if (!expected) return false;
    const currentValue = recruiterText(control?.value || "");
    if (currentValue !== expected) return true;
    return [...document.querySelectorAll("[data-test-facet-pill],[role='group'],button,span")]
      .filter((node) => node !== control && visibleElement(node))
      .some((node) => recruiterText(node.textContent || "").includes(expected));
  }

  async function submitRecruiterSearch(control, query) {
    const before = recruiterSearchResultState({ allowTransient: true }).signature;
    const action = await recruiterBooleanSearchAction(control);
    if (action) {
      if (!action.id) throw new Error("Recruiter global search action DOM contract mismatch");
      if (control.getAttribute("aria-activedescendant") !== action.id) {
        pressRecruiterKey(control, "ArrowDown");
        await wait(100);
      }
      if (control.getAttribute("aria-activedescendant") !== action.id) {
        throw new Error("Recruiter global search action DOM contract mismatch");
      }
      pressRecruiterKey(control, "Enter");
    } else {
      pressRecruiterKey(control, "Enter");
    }
    let stable = "";
    for (let attempt = 0; attempt < 30; attempt += 1) {
      await wait(400);
      if (challengeDetected()) {
        throw Object.assign(new Error("LinkedIn 要求安全验证"), { pauseReason: "challenge_detected" });
      }
      if (pageKind() !== "recruiter_search") {
        throw Object.assign(new Error("Recruiter 搜索提交后页面已变化"), { pauseReason: "page_changed" });
      }
      const current = recruiterSearchResultState({ allowTransient: true });
      if (!current.ready || current.signature === before) {
        stable = "";
        continue;
      }
      if (stable === current.signature) return;
      stable = current.signature;
    }
    throw new Error("Recruiter 搜索提交后置条件不满足");
  }

  async function resetRecruiterSearchState() {
    const buttons = [...document.querySelectorAll("button,[role='button']")].filter((node) =>
      visibleElement(node) && recruiterText(node.textContent) === "清除搜索"
    );
    if (buttons.length > 1) throw new Error("Recruiter search reset DOM contract mismatch");
    if (buttons.length === 1) {
      buttons[0].click();
      await wait(400);
    }
  }

  function recruiterSearchAlreadyActive(control, query) {
    const expected = recruiterText(query || "");
    if (!expected || recruiterText(control?.value || "") !== expected) return false;
    const clearButtons = [...document.querySelectorAll("button,[role='button']")].filter((node) =>
      visibleElement(node) && recruiterText(node.textContent) === "清除搜索"
    );
    return clearButtons.length === 1 && recruiterSearchResultState().ready;
  }

  async function recruiterFilterControl(key) {
    recruiterLocale();
    const spec = RECRUITER_FILTERS[key];
    let scope = recruiterFacetScope(key);
    const matchesInput = (node) => {
      const accessibleName = recruiterAccessibleName(node);
      return (spec.placeholders || [spec.placeholder]).includes(node.getAttribute("placeholder")) ||
        (spec.ariaLabels || []).some((label) => accessibleName === recruiterText(label));
    };
    const inputs = () => {
      const scoped = [...scope.querySelectorAll("input,textarea")].filter(matchesInput);
      if (key === "profile_language" && scope !== document && !scoped.length) {
        return [...scope.querySelectorAll("input,textarea")].filter((node) => visibleElement(node));
      }
      if (scoped.length || key !== "title" || scope !== document) return [...new Set(scoped)];
      // The calling-candidate overlay is only a fallback when the active
      // title facet has no own input. Both controls can be visible at once.
      return [...document.querySelectorAll(
        "[data-test-calling-job-typeahead] input[placeholder='输入职位头衔…']," +
        "#calling-overlay-typeahead input[placeholder='输入职位头衔…']"
      )].filter(matchesInput);
    };
    // An already-open facet has no add button. Prefer its unique visible
    // typeahead before trying to open the facet, otherwise an open panel is
    // mistaken for a missing filter panel.
    const visibleInputs = inputs().filter((node) => visibleElement(node));
    if (visibleInputs.length === 1) return visibleInputs[0];
    if (visibleInputs.length > 1) throw new Error(`Recruiter ${key} DOM contract mismatch`);
    const buttons = () => [...scope.querySelectorAll("button,[role='button']")].filter((node) =>
      spec.buttonAria
        ? node.getAttribute("aria-label") === spec.buttonAria
        : (node.textContent || "").normalize("NFKC").trim() === spec.buttonText
    );
    if (!buttons().some((node) => visibleElement(node))) {
      const toggles = () => [...document.querySelectorAll("button,[role='button']")]
        .filter((node) => (node.textContent || "").normalize("NFKC").trim() === "显示筛选条件");
      (await oneVisibleAfterRetry(toggles, "filter panel")).click();
      scope = recruiterFacetScope(key);
    }
    (await oneVisibleAfterRetry(buttons, key)).click();
    // Recruiter replaces the whole facet section when edit mode opens. The
    // pre-click scope is detached, so resolve the live section before looking
    // up its typeahead input.
    scope = recruiterFacetScope(key);
    return oneVisibleAfterRetry(inputs, key);
  }

  function recruiterFilterValues(key, value) {
    const values = Array.isArray(value) ? value : [value];
    const max = key === "keywords" ? 500 : 200;
    if (values.length > 20 || values.some((item) =>
      !["string", "number"].includes(typeof item) || !String(item).trim() || String(item).length > max
    )) throw new Error(`Recruiter ${key} filter contract mismatch`);
    return values.map((item) => String(item).trim());
  }

  function recruiterFacetScope(key) {
    const spec = RECRUITER_FILTERS[key];
    const labels = {
      title: ["职位头衔", "职位"],
      location: ["所在地点", "地点", "地区"],
      current_company: ["公司"],
      keywords: ["关键词"],
      profile_language: ["语言", "口语", "语言能力"],
      years_of_experience: ["经验年限", "工作年限"],
    }[key] || [];
    const sections = [...document.querySelectorAll("section.search-facet, .search-facet")]
      .filter((section) => visibleElement(section));
    const buttonSections = sections.filter((section) => [...section.querySelectorAll("button,[role='button']")]
      .some((node) => visibleElement(node) && (
        (spec.buttonAria && node.getAttribute("aria-label") === spec.buttonAria) ||
        (spec.buttonText && recruiterText(node.textContent) === recruiterText(spec.buttonText))
      )));
    if (buttonSections.length > 1) throw new Error(`Recruiter ${key} facet DOM contract mismatch`);
    if (buttonSections.length === 1) return buttonSections[0];
    const headingSections = sections.filter((section) => {
      const headingValues = [
        section.getAttribute("aria-label") || "",
        ...[...section.querySelectorAll(".search-facet__title, h2, [data-test-search-facet-title]")]
            .map((node) => node.textContent || node.getAttribute("aria-label") || ""),
      ].map(recruiterText);
      if (key === "current_company") {
        return headingValues.some((heading) => heading === "公司");
      }
      const headings = headingValues.join(" ");
      return labels.some((label) => headings.includes(recruiterText(label)));
    });
    if (headingSections.length > 1) throw new Error(`Recruiter ${key} facet DOM contract mismatch`);
    return headingSections[0] || document;
  }

  function recruiterFacetRoot(control) {
    return control?.closest("section.search-facet, .search-facet, [data-test-calling-job-typeahead], #calling-overlay-typeahead, .ts-common-typeahead") || document;
  }

  function recruiterAccessibleName(node) {
    if (!node) return "";
    const labelledBy = (node.getAttribute("aria-labelledby") || "").split(/\s+/)
      .filter(Boolean)
      .map((id) => document.getElementById(id)?.textContent || "");
    return recruiterText([
      node.getAttribute("aria-label") || "",
      ...labelledBy,
      node.getAttribute("title") || "",
    ].join(" "));
  }

  function recruiterDomDistance(left, right) {
    const leftPath = [];
    const rightPath = [];
    for (let current = left; current; current = current.parentElement) leftPath.push(current);
    for (let current = right; current; current = current.parentElement) rightPath.push(current);
    let leftIndex = leftPath.length - 1;
    let rightIndex = rightPath.length - 1;
    while (leftIndex >= 0 && rightIndex >= 0 && leftPath[leftIndex] === rightPath[rightIndex]) {
      leftIndex -= 1;
      rightIndex -= 1;
    }
    return (leftIndex + 1) + (rightIndex + 1);
  }

  function recruiterNearestPortalListbox(control, candidates) {
    if (!control || candidates.length === 0) return null;
    const controlRect = control.getBoundingClientRect();
    const rects = candidates.map((node) => ({ node, rect: node.getBoundingClientRect() }));
    const haveGeometry = [controlRect, ...rects.map(({ rect }) => rect)].every((rect) =>
      rect.width > 0 && rect.height > 0
    );
    const scored = rects.map(({ node, rect }) => {
      if (haveGeometry) {
        const controlX = controlRect.left + controlRect.width / 2;
        const controlY = controlRect.top + controlRect.height / 2;
        const nodeX = rect.left + rect.width / 2;
        const nodeY = rect.top + rect.height / 2;
        return { node, score: Math.hypot(controlX - nodeX, controlY - nodeY) };
      }
      return { node, score: recruiterDomDistance(control, node) };
    }).sort((left, right) => left.score - right.score);
    const [nearest, runnerUp] = scored;
    if (!nearest || !Number.isFinite(nearest.score)) return null;
    // A tie is evidence that the page exposed more than one plausible
    // owner. Keep the contract fail-closed instead of guessing a portal.
    if (runnerUp && Math.abs(nearest.score - runnerUp.score) <= 0.5) return null;
    return nearest.node;
  }

  function recruiterTypeaheadListboxes(control, facetRoot) {
    const typeaheadRoot = control?.closest(".artdeco-typeahead, .ts-common-typeahead");
    const local = typeaheadRoot
      ? [...typeaheadRoot.querySelectorAll("[role='listbox']")]
      : [];
    const facet = facetRoot && facetRoot !== document
      ? [...facetRoot.querySelectorAll("[role='listbox']")]
      : [];
    const controlledIds = new Set([
      ...(control?.getAttribute("aria-controls") || "").split(/\s+/),
      ...(control?.getAttribute("aria-owns") || "").split(/\s+/),
    ].filter(Boolean));
    const activeOption = control?.getAttribute("aria-activedescendant")
      ? document.getElementById(control.getAttribute("aria-activedescendant"))
      : null;
    // Recruiter may portal the live suggestions under document.body while
    // leaving a stale or recommendation list inside the facet. Only import a
    // portaled listbox when the active control explicitly owns/controls it.
    const controlledGlobal = [...document.querySelectorAll("[role='listbox']")].filter((node) =>
      (node.id && controlledIds.has(node.id)) || Boolean(activeOption && node.contains(activeOption))
    );
    const known = [...new Set([...local, ...facet, ...controlledGlobal])];
    const visible = known.filter((node) => visibleElement(node));
    if (!control || !visibleElement(control) ||
        visible.some((node) => recruiterAccessibleName(node).includes("输入建议"))) {
      return visible;
    }
    // Some Recruiter builds portal the live listbox under <body> without
    // aria-controls/aria-owns. Only adopt a unique live portal, or a unique
    // nearest one when several are visible; recommendation lists are
    // intentionally excluded by the accessible-name check.
    const knownSet = new Set(known);
    const portals = [...document.querySelectorAll("[role='listbox']")]
      .filter((node) => !knownSet.has(node) && visibleElement(node))
      .filter((node) => recruiterAccessibleName(node).includes("输入建议"));
    if (portals.length === 1) return [...visible, portals[0]];
    const nearest = recruiterNearestPortalListbox(control, portals);
    return nearest ? [...visible, nearest] : visible;
  }

  function recruiterUniqueOptions(options) {
    const seen = new Set();
    return options.filter((node) => {
      const key = `${recruiterText(node.textContent)}\u0000${node.getAttribute("data-live-test-result") || ""}`;
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
  }

  function recruiterText(value) {
    let text = String(value || "");
    for (let pass = 0; pass < 3; pass += 1) {
      const decoded = text.replace(/&(?:amp|lt|gt|quot|apos|nbsp|#(?:x[0-9a-f]+|[0-9]+));/gi, (entity) => {
        const token = entity.slice(1, -1).toLowerCase();
        const named = { amp: "&", lt: "<", gt: ">", quot: '"', apos: "'", nbsp: " " }[token];
        if (named !== undefined) return named;
        const codePoint = token.startsWith("#x")
          ? Number.parseInt(token.slice(2), 16)
          : Number.parseInt(token.slice(1), 10);
        return Number.isInteger(codePoint) && codePoint >= 0 && codePoint <= 0x10ffff &&
          !(codePoint >= 0xd800 && codePoint <= 0xdfff)
          ? String.fromCodePoint(codePoint)
          : entity;
      });
      if (decoded === text) break;
      text = decoded;
    }
    return text.normalize("NFKC").replace(/\s+/g, " ").trim();
  }

  function recruiterScopedNodes(root, selector, predicate = () => true) {
    const liveRoot = root?.isConnected ? root : document;
    const local = [...liveRoot.querySelectorAll(selector)].filter(predicate);
    if (local.length || liveRoot === document) return local;
    return [...document.querySelectorAll(selector)].filter(predicate);
  }

  function setRecruiterControlValue(control, value) {
    const prototype = control instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(prototype, "value")?.set;
    if (!setter || (!(control instanceof HTMLInputElement) && !(control instanceof HTMLTextAreaElement))) {
      throw new Error("Recruiter filter control contract mismatch");
    }
    control.focus();
    if (typeof control.setSelectionRange === "function") {
      const end = control.value.length;
      control.setSelectionRange(end, end);
    }
    setter.call(control, value);
    if (typeof control.setSelectionRange === "function") {
      control.setSelectionRange(value.length, value.length);
    }
    control.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "insertText", data: value }));
  }

  function pressRecruiterKey(control, key) {
    if (!control || !control.isConnected) throw new Error("Recruiter filter control contract mismatch");
    control.focus();
    for (const type of ["keydown", "keypress", "keyup"]) {
      control.dispatchEvent(new KeyboardEvent(type, {
        bubbles: true,
        cancelable: true,
        key,
        code: key,
        keyCode: key === "ArrowDown" ? 40 : 13,
        which: key === "ArrowDown" ? 40 : 13,
      }));
    }
  }

  function recruiterSelectedFilter(item, root = document) {
    const expected = recruiterText(item);
    const expectedAriaPrefix = recruiterText(`可以有，${expected}。`);
    return [...root.querySelectorAll("[role='group'][aria-label],[data-test-facet-pill]")].filter((group) => {
      if (!visibleElement(group)) return false;
      const aria = recruiterText(group.getAttribute("aria-label"));
      const title = recruiterText(group.getAttribute("title"));
      return aria.startsWith(expectedAriaPrefix) ||
        (group.matches("[data-test-facet-pill]") && title === expected);
    });
  }

  function recruiterFreeTextAdders(root, item) {
    const expected = recruiterText(item);
    return recruiterScopedNodes(root, "[role='listbox']", (listbox) =>
      visibleElement(listbox) &&
      [...listbox.querySelectorAll("[role='alert']")].some((alert) =>
        recruiterText(alert.textContent).startsWith("按回车键添加") &&
        [...listbox.querySelectorAll("strong")].some((strong) => recruiterText(strong.textContent) === expected)
      )
    );
  }

  function recruiterContractPhase(message) {
    const value = String(message || "");
    if (/facet DOM/.test(value)) return "facet";
    if (/typeahead DOM/.test(value)) return "typeahead";
    if (/selected filter/.test(value)) return "selection";
    if (/(?:option|postcondition)/.test(value)) return "postcondition";
    if (/(?: DOM|filter control)/.test(value)) return "control";
    return "unknown";
  }

  function recruiterContractCode(message, key) {
    const value = String(message || "");
    const match = value.match(/^Recruiter [a-z_]+ (.+?)(?: DOM)? contract mismatch$/);
    const suffix = match?.[1] || (value.includes("后置条件") ? "postcondition" : "contract");
    return `${key}.${suffix.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "") || "contract"}`.slice(0, 64);
  }

  function recruiterContractSnapshot(key, item) {
    const spec = RECRUITER_FILTERS[key] || {};
    const labels = {
      title: ["职位头衔", "职位"],
      location: ["所在地点", "地点", "地区"],
      current_company: ["公司"],
      keywords: ["关键词"],
      profile_language: ["语言", "口语", "语言能力"],
      years_of_experience: ["经验年限", "工作年限"],
    }[key] || [];
    const sections = [...document.querySelectorAll("section.search-facet, .search-facet")]
      .filter((section) => visibleElement(section));
    const facets = sections.filter((section) => {
      const headings = [
        section.getAttribute("aria-label") || "",
        ...[...section.querySelectorAll(".search-facet__title, h2, [data-test-search-facet-title]")]
          .map((node) => node.textContent || node.getAttribute("aria-label") || ""),
      ].map(recruiterText);
      if (key === "current_company") return headings.some((heading) => heading === "公司");
      return headings.some((heading) => labels.some((label) => heading.includes(recruiterText(label))));
    });
    const roots = facets.length ? facets : [document];
    const inputs = [...new Set(roots.flatMap((root) => [...root.querySelectorAll("input,textarea")]))]
      .filter((node) => visibleElement(node))
      .filter((node) => {
        const accessibleName = recruiterAccessibleName(node);
        return (spec.placeholders || [spec.placeholder]).filter(Boolean).includes(node.getAttribute("placeholder")) ||
          (spec.ariaLabels || []).some((label) => accessibleName === recruiterText(label));
      });
    const selectionRoot = facets.length === 1 ? facets[0] : document;
    const selected = recruiterSelectedFilter(item, selectionRoot);
    const localListboxes = roots.flatMap((root) => [...root.querySelectorAll("[role='listbox']")]);
    const controlledListboxes = inputs.length === 1
      ? recruiterTypeaheadListboxes(inputs[0], recruiterFacetRoot(inputs[0]))
      : [];
    const listboxes = [...new Set([...localListboxes, ...controlledListboxes])]
      .filter((node) => visibleElement(node));
    const liveListboxes = listboxes.filter((node) => recruiterAccessibleName(node).includes("输入建议"));
    const recommendationListboxes = listboxes.filter((node) => recruiterAccessibleName(node).includes("选择推荐值"));
    const options = recruiterUniqueOptions(liveListboxes.flatMap((listbox) =>
      [...listbox.querySelectorAll("[role='option']")]
        .filter((node) => visibleElement(node) && recruiterText(node.textContent))
    ));
    const expected = recruiterText(item);
    const exactOptions = options.filter((node) => recruiterText(node.textContent) === expected);
    const rank0Options = options.filter((node) => node.getAttribute("data-live-test-result") === "0");
    const snapshot = {
      contract_version: RECRUITER_DOM_CONTRACT_VERSION,
      page_kind: pageKind(),
      facet_count: facets.length,
      input_count: inputs.length,
      input_value_present: inputs.some((node) => Boolean(node.value)),
      selected_count: selected.length,
      listbox_count: listboxes.length,
      live_listbox_count: liveListboxes.length,
      recommendation_listbox_count: recommendationListboxes.length,
      option_count: options.length,
      exact_option_count: exactOptions.length,
      rank0_option_count: rank0Options.length,
    };
    const issues = [];
    if (snapshot.facet_count === 0) issues.push("facet_missing");
    if (snapshot.facet_count > 1) issues.push("facet_ambiguous");
    if (snapshot.input_count === 0) issues.push("input_missing");
    if (snapshot.input_count > 1) issues.push("input_ambiguous");
    if (snapshot.selected_count === 0) issues.push("selected_missing");
    if (snapshot.selected_count > 1) issues.push("selected_ambiguous");
    if (snapshot.live_listbox_count === 0 && snapshot.input_value_present) issues.push("live_listbox_missing");
    if (snapshot.recommendation_listbox_count > 0 && snapshot.live_listbox_count === 0) issues.push("recommendation_only");
    if (snapshot.option_count === 0 && snapshot.selected_count === 0) issues.push("option_missing");
    if (snapshot.rank0_option_count > 1) issues.push("multiple_rank0");
    if (snapshot.exact_option_count > 1 && snapshot.rank0_option_count === 0) issues.push("duplicate_exact_without_rank0");
    if (snapshot.input_value_present && snapshot.selected_count > 0) issues.push("input_not_cleared");
    return { snapshot, issues };
  }

  function recruiterRecoveryInputMatches(node, spec) {
    const accessibleName = recruiterAccessibleName(node);
    return (spec.placeholders || [spec.placeholder]).filter(Boolean).includes(node.getAttribute("placeholder")) ||
      (spec.ariaLabels || []).some((label) => accessibleName === recruiterText(label));
  }

  function recruiterRecoveryGlobalInputs() {
    return [...new Set(document.querySelectorAll(
      "input[role='combobox'][aria-label='搜索职位头衔、目标人才、关键词或布尔条件等'][placeholder='在此输入任意内容']," +
      "input[role='combobox'][aria-label='关闭搜索输入'][placeholder='在此输入任意内容']," +
      "[data-test-global-nav-copilot-typeahead] [data-test-copilot-typeahead-typeahead-input] input[role='combobox']," +
      "[data-test-global-nav-copilot-typeahead] #copilot-typeahead-typeahead-input input[role='combobox']," +
      "#copilot-typeahead-form #copilot-typeahead-typeahead-input input[role='combobox']," +
      "[data-test-global-nav-copilot-typeahead] textarea[data-test-copilot-chat-input][placeholder='在此输入任何内容即可开始']," +
      "#copilot-typeahead-form textarea[data-test-copilot-chat-input][placeholder='在此输入任何内容即可开始']"
    ))].filter((node) => visibleElement(node));
  }

  function recruiterRecoveryCandidateName(node, kind, value) {
    if (kind === "input") {
      return recruiterAccessibleName(node) || recruiterText(node.getAttribute("placeholder")) || "Recruiter 输入框";
    }
    if (kind === "free_text_add") return recruiterText(node.textContent) || `按回车键添加 ${value}`;
    return recruiterAccessibleName(node) || recruiterText(node.textContent) || recruiterText(value);
  }

  function recruiterRecoveryPostcondition(intent, filter, value, expectedSelection = value) {
    const expected = recruiterText(expectedSelection);
    if (intent === "submit_search") {
      const inputs = recruiterRecoveryGlobalInputs();
      const result = recruiterSearchResultState({ allowTransient: true });
      const chips = [...document.querySelectorAll("[data-test-facet-pill],[role='group'],button,span")]
        .filter((node) => visibleElement(node) && recruiterText(node.textContent).includes(recruiterText(value)));
      const clearButtons = [...document.querySelectorAll("button,[role='button']")].filter((node) =>
        visibleElement(node) && recruiterText(node.textContent) === "清除搜索"
      );
      const active = inputs.length === 1 && clearButtons.length === 1 && result.ready &&
        recruiterText(inputs[0].value) === recruiterText(value);
      return {
        contract_version: RECRUITER_DOM_CONTRACT_VERSION,
        page_kind: pageKind(),
        intent,
        filter,
        value: String(value).slice(0, 500),
        input_count: inputs.length,
        input_value_matches: inputs.filter((node) => recruiterText(node.value) === recruiterText(value)).length,
        selected_count: chips.length,
        result_ready: result.ready,
        result_signature: result.signature,
        satisfied: result.ready && (active || chips.length > 0),
      };
    }
    const spec = RECRUITER_FILTERS[filter];
    const scope = recruiterFacetScope(filter);
    const inputs = [...scope.querySelectorAll("input,textarea")]
      .filter((node) => visibleElement(node) && recruiterRecoveryInputMatches(node, spec));
    const selected = recruiterSelectedFilter(expected, scope);
    return {
      contract_version: RECRUITER_DOM_CONTRACT_VERSION,
      page_kind: pageKind(),
      intent,
      filter,
      value: String(value).slice(0, 500),
      expected_selection: String(expectedSelection).slice(0, 500),
      input_count: inputs.length,
      input_value_matches: inputs.filter((node) => recruiterText(node.value) === recruiterText(value)).length,
      input_cleared: inputs.every((node) => !node.value),
      selected_count: selected.length,
      satisfied: selected.length === 1,
    };
  }

  function recruiterRecoverySnapshotId() {
    recruiterCandidateSnapshotSequence += 1;
    const entropy = new Uint32Array(2);
    crypto.getRandomValues(entropy);
    return `recruiter-${recruiterCandidateSnapshotSequence.toString(36)}-${[...entropy]
      .map((item) => item.toString(16).padStart(8, "0")).join("")}`;
  }

  function pruneRecruiterCandidateSnapshots() {
    const now = Date.now();
    for (const [snapshotId, snapshot] of recruiterCandidateSnapshots) {
      if (snapshot.used || now - snapshot.createdAt > RECRUITER_CANDIDATE_TTL_MS) {
        recruiterCandidateSnapshots.delete(snapshotId);
      }
    }
    while (recruiterCandidateSnapshots.size > 8) {
      recruiterCandidateSnapshots.delete(recruiterCandidateSnapshots.keys().next().value);
    }
  }

  function recruiterRecoveryFacetButtons(filter, scope) {
    const spec = RECRUITER_FILTERS[filter];
    return [...scope.querySelectorAll("button,[role='button']")].filter((node) =>
      visibleElement(node) && (
        (spec.buttonAria && node.getAttribute("aria-label") === spec.buttonAria) ||
        (spec.buttonText && recruiterText(node.textContent) === recruiterText(spec.buttonText))
      )
    );
  }

  function recruiterRecoveryCandidateSnapshot(intent, filter, value) {
    if (pageKind() !== "recruiter_search") throw new Error("Recruiter 自修复页面无效");
    recruiterLocale();
    const targetValue = String(value).trim();
    const snapshotId = recruiterRecoverySnapshotId();
    const records = [];
    const seenNodes = new Set();
    const addCandidate = (kind, node, allowedActions, details = {}) => {
      if (!node || !visibleElement(node) || seenNodes.has(node) || records.length >= 25) return;
      const name = recruiterRecoveryCandidateName(node, kind, targetValue).slice(0, 500);
      if (!name) return;
      const enabled = !node.disabled && node.getAttribute("aria-disabled") !== "true";
      const actions = enabled
        ? allowedActions.filter((action) => RECRUITER_RECOVERY_ACTIONS.has(action))
        : [];
      if (!actions.length) return;
      seenNodes.add(node);
      const candidateId = `candidate-${records.length + 1}`;
      records.push({
        candidate_id: candidateId,
        kind,
        name,
        exact_match: details.exactMatch === true,
        enabled,
        allowed_actions: actions,
        node,
        control: details.control || null,
        guard_name: name,
        guard_text: recruiterText(node.textContent),
        guard_accessible_name: recruiterAccessibleName(node),
        postcondition_value: details.postconditionValue || targetValue,
      });
    };

    if (intent === "submit_search") {
      const inputs = recruiterRecoveryGlobalInputs();
      for (const control of inputs) {
        const exactMatch = recruiterText(control.value) === recruiterText(targetValue);
        addCandidate("input", control, ["focus", "type", ...(exactMatch ? ["press_enter"] : [])], {
          exactMatch,
          control,
        });
        const controlledIds = new Set(String(control.getAttribute("aria-controls") || "").split(/\s+/).filter(Boolean));
        const roots = [...controlledIds].map((id) => document.getElementById(id))
          .filter((node) => node && visibleElement(node));
        for (const option of roots.flatMap((root) => [...root.querySelectorAll(
          "li[role='link'],[role='option'],button,[role='button']"
        )])) {
          const name = recruiterText(option.textContent);
          if (!name || !name.includes(recruiterText(targetValue))) continue;
          addCandidate("option", option, option.id ? ["press_arrow_down_enter"] : [], {
            exactMatch: name === recruiterText(targetValue),
            control,
            postconditionValue: targetValue,
          });
        }
      }
    } else {
      const spec = RECRUITER_FILTERS[filter];
      const scope = recruiterFacetScope(filter);
      const selected = recruiterSelectedFilter(targetValue, scope);
      for (const node of selected) addCandidate("selected", node, ["noop"], { exactMatch: true });
      const inputs = [...scope.querySelectorAll("input,textarea")]
        .filter((node) => visibleElement(node) && recruiterRecoveryInputMatches(node, spec));
      for (const control of inputs) {
        addCandidate("input", control, ["focus", "type"], {
          exactMatch: recruiterText(control.value) === recruiterText(targetValue),
          control,
        });
        const facetRoot = recruiterFacetRoot(control);
        for (const listbox of recruiterFreeTextAdders(facetRoot, targetValue)) {
          addCandidate("free_text_add", listbox, ["press_enter"], {
            exactMatch: true,
            control,
          });
        }
        const listboxes = recruiterTypeaheadListboxes(control, facetRoot)
          .filter((node) => recruiterAccessibleName(node).includes("输入建议"));
        const options = recruiterUniqueOptions(listboxes.flatMap((listbox) =>
          [...listbox.querySelectorAll("[role='option']")]
            .filter((node) => visibleElement(node) && recruiterText(node.textContent))
        ));
        for (const option of options) {
          const name = recruiterText(option.textContent);
          const keyboardActions = option.id ? ["press_arrow_down_enter"] : [];
          addCandidate("option", option, ["click", ...keyboardActions], {
            exactMatch: name === recruiterText(targetValue),
            control,
            postconditionValue: name,
          });
        }
      }
      for (const button of recruiterRecoveryFacetButtons(filter, scope)) {
        addCandidate("button", button, ["click"]);
      }
      if (scope === document) {
        for (const button of [...document.querySelectorAll("button,[role='button']")].filter((node) =>
          visibleElement(node) && recruiterText(node.textContent) === "显示筛选条件"
        )) addCandidate("button", button, ["click"]);
      }
    }

    const publicCandidates = records.map((record) => ({
      candidate_id: record.candidate_id,
      kind: record.kind,
      name: record.name,
      exact_match: record.exact_match,
      enabled: record.enabled,
      allowed_actions: record.allowed_actions,
    }));
    const allowedPairs = publicCandidates.flatMap((candidate) => candidate.allowed_actions.map((action) => ({
      type: "object",
      additionalProperties: false,
      required: ["candidate_id", "action"],
      properties: {
        candidate_id: { const: candidate.candidate_id },
        action: { const: action },
      },
    })));
    const snapshot = {
      snapshotId,
      createdAt: Date.now(),
      used: false,
      pageUrl: canonicalNavigationUrl(location.href),
      intent,
      filter,
      value: targetValue,
      records,
    };
    pruneRecruiterCandidateSnapshots();
    recruiterCandidateSnapshots.set(snapshotId, snapshot);
    return {
      snapshot_id: snapshotId,
      contract_version: RECRUITER_DOM_CONTRACT_VERSION,
      page_kind: pageKind(),
      intent,
      filter,
      value: targetValue,
      candidates: publicCandidates,
      action_schema: {
        type: "object",
        additionalProperties: false,
        oneOf: allowedPairs.length ? allowedPairs : [{
          type: "object",
          additionalProperties: false,
          required: ["action"],
          properties: { action: { const: "fail" } },
        }],
      },
      postcondition: recruiterRecoveryPostcondition(intent, filter, targetValue),
    };
  }

  function recruiterRecoverySnapshot(snapshotId) {
    pruneRecruiterCandidateSnapshots();
    const snapshot = recruiterCandidateSnapshots.get(snapshotId);
    if (!snapshot || snapshot.used || Date.now() - snapshot.createdAt > RECRUITER_CANDIDATE_TTL_MS) {
      throw new Error("Recruiter 候选快照已失效");
    }
    if (pageKind() !== "recruiter_search" || canonicalNavigationUrl(location.href) !== snapshot.pageUrl) {
      throw new Error("Recruiter 候选快照页面已变化");
    }
    return snapshot;
  }

  function recruiterRecoveryValidateRecord(record) {
    if (!record?.node?.isConnected || !visibleElement(record.node) || !record.enabled ||
        recruiterRecoveryCandidateName(record.node, record.kind, record.postcondition_value) !== record.guard_name ||
        recruiterText(record.node.textContent) !== record.guard_text ||
        recruiterAccessibleName(record.node) !== record.guard_accessible_name) {
      throw new Error("Recruiter 候选元素已变化");
    }
    if (record.control && (!record.control.isConnected || !visibleElement(record.control))) {
      throw new Error("Recruiter 候选输入框已变化");
    }
  }

  async function executeRecruiterRecoveryAction(snapshotId, candidateId, action) {
    const snapshot = recruiterRecoverySnapshot(snapshotId);
    const record = snapshot.records.find((candidate) => candidate.candidate_id === candidateId);
    if (!record || !record.allowed_actions.includes(action) || !RECRUITER_RECOVERY_ACTIONS.has(action)) {
      throw new Error("Recruiter 候选动作越界");
    }
    recruiterRecoveryValidateRecord(record);
    snapshot.used = true;
    if (action === "noop") {
      if (record.kind !== "selected") throw new Error("Recruiter 候选动作越界");
    } else if (action === "focus") {
      if (record.kind !== "input") throw new Error("Recruiter 候选动作越界");
      record.node.focus();
    } else if (action === "type") {
      if (record.kind !== "input") throw new Error("Recruiter 候选动作越界");
      setRecruiterControlValue(record.node, snapshot.value);
    } else if (action === "click") {
      if (!["button", "option"].includes(record.kind)) throw new Error("Recruiter 候选动作越界");
      record.node.click();
    } else if (action === "press_enter") {
      const control = record.kind === "input" ? record.node : record.control;
      if (!control || !["input", "free_text_add"].includes(record.kind)) {
        throw new Error("Recruiter 候选动作越界");
      }
      pressRecruiterKey(control, "Enter");
    } else if (action === "press_arrow_down_enter") {
      if (record.kind !== "option" || !record.control || !record.node.id) {
        throw new Error("Recruiter 候选动作越界");
      }
      let activeId = record.control.getAttribute("aria-activedescendant");
      for (let attempt = 0; attempt < 12 && activeId !== record.node.id; attempt += 1) {
        pressRecruiterKey(record.control, "ArrowDown");
        await wait(50);
        activeId = record.control.getAttribute("aria-activedescendant");
      }
      if (activeId !== record.node.id) throw new Error("Recruiter 候选项无法安全激活");
      pressRecruiterKey(record.control, "Enter");
    }
    let postcondition = recruiterRecoveryPostcondition(
      snapshot.intent, snapshot.filter, snapshot.value, record.postcondition_value,
    );
    if (["click", "press_enter", "press_arrow_down_enter", "noop"].includes(action)) {
      for (let attempt = 0; attempt < 5 && !postcondition.satisfied; attempt += 1) {
        await wait(100);
        postcondition = recruiterRecoveryPostcondition(
          snapshot.intent, snapshot.filter, snapshot.value, record.postcondition_value,
        );
      }
    }
    recruiterCandidateSnapshots.delete(snapshotId);
    return {
      snapshot_id: snapshotId,
      candidate_id: candidateId,
      action,
      postcondition,
    };
  }

  function recruiterContractFailure(error, diagnostics, key, item, valueIndex) {
    const caught = error instanceof Error ? error : new Error(String(error || "页面动作失败"));
    let observation;
    try {
      observation = recruiterContractSnapshot(key, item);
    } catch (_snapshotError) {
      observation = {
        snapshot: {
          contract_version: RECRUITER_DOM_CONTRACT_VERSION,
          page_kind: pageKind(),
          facet_count: 0,
          input_count: 0,
          input_value_present: false,
          selected_count: 0,
          listbox_count: 0,
          live_listbox_count: 0,
          recommendation_listbox_count: 0,
          option_count: 0,
          exact_option_count: 0,
          rank0_option_count: 0,
        },
        issues: ["snapshot_unavailable"],
      };
    }
    diagnostics.push({
      filter: key,
      value_index: valueIndex,
      value: String(item).slice(0, 500),
      status: "failed",
      phase: recruiterContractPhase(caught.message),
      code: recruiterContractCode(caught.message, key),
      contract_version: RECRUITER_DOM_CONTRACT_VERSION,
      page_kind: pageKind(),
      issues: observation.issues,
      snapshot: observation.snapshot,
    });
    caught.contractDiagnostics = diagnostics.slice(0, 50);
    return caught;
  }

  async function applyRecruiterFilter(key, value, diagnostics = []) {
    const spec = RECRUITER_FILTERS[key];
    const values = recruiterFilterValues(key, value);
    let currentDiagnostic = null;
    try {
    for (let valueIndex = 0; valueIndex < values.length; valueIndex += 1) {
      if (currentDiagnostic) diagnostics.push({
        ...currentDiagnostic,
        status: "completed",
        phase: "unknown",
        code: "completed",
        contract_version: RECRUITER_DOM_CONTRACT_VERSION,
        page_kind: pageKind(),
        issues: [],
      });
      const item = values[valueIndex];
      currentDiagnostic = {
        filter: key,
        value_index: valueIndex,
        value: item,
      };
      const selectionScope = recruiterFacetScope(key);
      // A selected chip is already the postcondition, including the keyword
      // chip created by open_search. In that state Recruiter intentionally
      // unmounts the edit input, so requiring it would turn an applied value
      // into a false input_missing failure.
      const selected = recruiterSelectedFilter(item, selectionScope);
      if (selected.length > 1) throw new Error(`Recruiter ${key} selected filter contract mismatch`);
      if (selected.length === 1) continue;
      // LinkedIn replaces the typeahead input after each selected chip.
      // Re-resolve it per value so later terms never target a detached node.
      let control = await recruiterFilterControl(key);
      let facetRoot = recruiterFacetRoot(control);
      let currentSelectionScope = recruiterFacetScope(key);
      const selectedAfterControl = recruiterSelectedFilter(item, currentSelectionScope);
      if (selectedAfterControl.length > 1) throw new Error(`Recruiter ${key} selected filter contract mismatch`);
      if (selectedAfterControl.length === 1) {
        if (control.value) setRecruiterControlValue(control, "");
        continue;
      }
      setRecruiterControlValue(control, item);
      if (spec.freeText) {
        control.dispatchEvent(new Event("change", { bubbles: true }));
        control.blur();
        continue;
      }
      let option = null;
      let committed = false;
      let optionPostcondition = null;
      let optionRequiresEmptyInput = false;
      let optionUseKeyboard = false;
      let typeaheadAmbiguitySignature = "";
      // Some Recruiter builds commit free-text title terms directly to a
      // chip and never expose a role=option or "按回车键添加" listbox.
      // Observe both outcomes in the same bounded window so this path does
      // not add a second wait before the existing Enter fallback.
      for (let attempt = 0; attempt < 30; attempt += 1) {
        currentSelectionScope = recruiterFacetScope(key);
        // Recruiter may commit an English company value directly while its
        // recommendation list remains mounted. Treat the committed chip as
        // authoritative before inspecting unrelated recommendation options.
        const selectedBeforeOptions = recruiterSelectedFilter(item, currentSelectionScope);
        if (selectedBeforeOptions.length > 1) {
          throw new Error(`Recruiter ${key} selected filter contract mismatch`);
        }
        if (selectedBeforeOptions.length === 1) {
          committed = true;
          break;
        }
        if (!control.isConnected || !facetRoot.isConnected) {
          control = await recruiterFilterControl(key);
          facetRoot = recruiterFacetRoot(control);
          currentSelectionScope = recruiterFacetScope(key);
          if (control.value !== item) setRecruiterControlValue(control, item);
        }
        if (recruiterFreeTextAdders(facetRoot, item).length === 1) break;
        const visibleOptions = recruiterScopedNodes(facetRoot, "[role='option']")
          .filter((node) => visibleElement(node))
          .filter((node) => recruiterText(node.textContent))
          // Never let a visible option from another facet make a title
          // typeahead ambiguous. EasyHire scopes each typeahead to its live
          // facet; preserve that boundary even when Recruiter renders the
          // recommendation list near document.body.
          .filter((node) => {
            if (key !== "title") return true;
            const listbox = node.closest("[role='listbox']");
            return Boolean(listbox) && (facetRoot === document || facetRoot.contains(listbox));
          });
        const currentCompanyListboxes = key === "current_company"
          ? recruiterTypeaheadListboxes(control, facetRoot)
          : [];
        const currentCompanyListboxSet = new Set(currentCompanyListboxes);
        const currentCompanyTypeaheadOptions = key === "current_company"
          ? recruiterUniqueOptions(currentCompanyListboxes.flatMap((listbox) =>
            [...listbox.querySelectorAll("[role='option']")]
              .filter((node) => visibleElement(node) && recruiterText(node.textContent))
          ).filter((node) => {
            const listbox = node.closest("[role='listbox']");
            return listbox && currentCompanyListboxSet.has(listbox) &&
              recruiterAccessibleName(listbox).includes("输入建议");
          }))
          : visibleOptions;
        const exactOptions = currentCompanyTypeaheadOptions.filter((node) => recruiterText(node.textContent) === recruiterText(item));
        const localizedTitleOptions = visibleOptions.filter((node) => {
          const listbox = node.closest("[role='listbox']");
          return key === "title" && facetRoot !== document && listbox && visibleElement(listbox) &&
            recruiterText(listbox.getAttribute("aria-label")).includes("输入建议");
        });
        const rankedLocationOptions = visibleOptions.filter((node) => {
          const listbox = node.closest("[role='listbox']");
          return key === "location" && exactOptions.length === 0 &&
            node.getAttribute("data-live-test-result") === "0" && listbox && facetRoot.contains(listbox) && visibleElement(listbox) &&
            recruiterText(listbox.getAttribute("aria-label")) === "输入建议结果";
        });
        const rankedCurrentCompanyOptions = currentCompanyTypeaheadOptions.filter((node) => {
          const listbox = node.closest("[role='listbox']");
          return key === "current_company" &&
            node.getAttribute("data-live-test-result") === "0" && listbox &&
            visibleElement(listbox) && currentCompanyListboxSet.has(listbox) &&
            recruiterAccessibleName(listbox).includes("输入建议");
        });
        const currentCompanyOptions = key === "current_company" && exactOptions.length === 0 &&
          rankedCurrentCompanyOptions.length === 0
          ? currentCompanyTypeaheadOptions
          : [];
        // Recruiter can expose the same company twice (for example Meta at
        // ranks 0 and 1). A unique rank-0 option is the authoritative live
        // suggestion, so it must win over duplicate exact text matches.
        const ambiguity = (key !== "current_company" && exactOptions.length > 1) ||
            (exactOptions.length === 0 && key === "title" && facetRoot !== document && visibleOptions.length > 1) ||
            localizedTitleOptions.length > 1 || rankedLocationOptions.length > 1 ||
            rankedCurrentCompanyOptions.length > 1 ||
            (key === "current_company" && rankedCurrentCompanyOptions.length === 0 && exactOptions.length > 1) ||
            currentCompanyOptions.length > 1;
        if (ambiguity) {
          const signature = [
            exactOptions.map((node) => `${recruiterText(node.textContent)}:${node.getAttribute("data-live-test-result") || ""}`),
            rankedCurrentCompanyOptions.map((node) => `${recruiterText(node.textContent)}:${node.getAttribute("data-live-test-result") || ""}`),
            currentCompanyOptions.map((node) => recruiterText(node.textContent)),
          ].flat().join("|");
          if (signature !== typeaheadAmbiguitySignature || attempt < 3) {
            typeaheadAmbiguitySignature = signature;
            if (attempt < 29) await wait(200);
            continue;
          }
          throw new Error(`Recruiter ${key} typeahead DOM contract mismatch`);
        }
        if (rankedCurrentCompanyOptions.length === 1) {
          const localizedCompany = recruiterText(rankedCurrentCompanyOptions[0].textContent);
          if (!localizedCompany) throw new Error(`Recruiter ${key} typeahead DOM contract mismatch`);
          const selected = recruiterSelectedFilter(localizedCompany, currentSelectionScope);
          if (selected.length > 1) throw new Error(`Recruiter ${key} selected filter contract mismatch`);
          if (selected.length === 1) {
            committed = true;
            break;
          }
          option = rankedCurrentCompanyOptions[0];
          optionPostcondition = localizedCompany;
          optionRequiresEmptyInput = true;
          optionUseKeyboard = true;
          break;
        }
        if (exactOptions.length === 1) {
          option = exactOptions[0];
          if (["current_company", "profile_language"].includes(key)) {
            optionPostcondition = recruiterText(option.textContent);
            optionRequiresEmptyInput = true;
          }
          break;
        }
        // 中文 Recruiter localizes the sole title suggestion while the input
        // and the value we submit remain the backend's English title.
        if (key === "title" && localizedTitleOptions.length === 1) {
          option = localizedTitleOptions[0];
          break;
        }
        if (rankedLocationOptions.length === 1) {
          const localizedLocation = recruiterText(rankedLocationOptions[0].textContent);
          if (!localizedLocation) throw new Error(`Recruiter ${key} typeahead DOM contract mismatch`);
          const selected = recruiterSelectedFilter(localizedLocation, currentSelectionScope);
          if (selected.length > 1) throw new Error(`Recruiter ${key} selected filter contract mismatch`);
          if (selected.length === 1) {
            committed = true;
            break;
          }
          option = rankedLocationOptions[0];
          optionPostcondition = localizedLocation;
          optionUseKeyboard = true;
          break;
        }
        if (currentCompanyOptions.length === 1) {
          const localizedCompany = recruiterText(currentCompanyOptions[0].textContent);
          if (!localizedCompany) throw new Error(`Recruiter ${key} typeahead DOM contract mismatch`);
          const selected = recruiterSelectedFilter(localizedCompany, currentSelectionScope);
          if (selected.length > 1) throw new Error(`Recruiter ${key} selected filter contract mismatch`);
          if (selected.length === 1) {
            committed = true;
            break;
          }
          option = currentCompanyOptions[0];
          optionPostcondition = localizedCompany;
          optionRequiresEmptyInput = true;
          break;
        }
        const selected = recruiterSelectedFilter(item, currentSelectionScope);
        if (selected.length > 1) throw new Error(`Recruiter ${key} selected filter contract mismatch`);
        if (selected.length === 1) {
          committed = true;
          break;
        }
      if (attempt < 29) await wait(400);
      }
      if (committed) {
        if (control.isConnected && control.value) setRecruiterControlValue(control, "");
        continue;
      }
      if (!option) {
        // Recruiter can expose an exact free-text company as a deliberate
        // "按回车键添加" row instead of a role=option. Re-resolve both
        // before using the Enter fallback; never act on a detached control
        // or an ambiguous listbox.
        const currentControl = await recruiterFilterControl(key);
        const currentFacetRoot = recruiterFacetRoot(currentControl);
        currentSelectionScope = recruiterFacetScope(key);
        const adders = recruiterFreeTextAdders(currentFacetRoot, item).filter((listbox) =>
          recruiterText(listbox.textContent).includes(recruiterText(`按回车键添加`))
        );
        if (adders.length !== 1) throw new Error(`Recruiter ${key} typeahead DOM contract mismatch`);
        if (currentControl.value !== item) setRecruiterControlValue(currentControl, item);
        currentControl.focus();
        for (const type of ["keydown", "keypress", "keyup"]) {
          currentControl.dispatchEvent(new KeyboardEvent(type, {
            bubbles: true, cancelable: true, key: "Enter", code: "Enter", keyCode: 13, which: 13,
          }));
        }
        let postEnterSelected = [];
        for (let settleAttempt = 0; settleAttempt < 5; settleAttempt += 1) {
          await wait(100);
          postEnterSelected = recruiterSelectedFilter(item, currentSelectionScope);
          if (!currentControl.value && postEnterSelected.length === 1) break;
        }
        if (currentControl.value || postEnterSelected.length !== 1) throw new Error(`Recruiter ${key} enter option contract mismatch`);
        continue;
      }
      // LinkedIn's live typeahead commits the localized location on the
      // keyboard path; HTMLElement.click() fires only a synthetic click and
      // leaves the option mounted without creating the chip.
      if (optionUseKeyboard) {
        let currentControl = await recruiterFilterControl(key);
        if (currentControl.value !== item) setRecruiterControlValue(currentControl, item);
        let activeId = currentControl.getAttribute("aria-activedescendant");
        // Recruiter may preserve the previous highlighted suggestion when the
        // input is rehydrated. Move upward through the bounded option list
        // until the fixed rank-0 option is active instead of treating that
        // benign state as a contract mismatch.
        for (let keyAttempt = 0; keyAttempt < 12 && option.id && activeId !== option.id; keyAttempt += 1) {
          pressRecruiterKey(currentControl, activeId ? "ArrowUp" : "ArrowDown");
          await wait(80);
          currentControl = await recruiterFilterControl(key);
          activeId = currentControl.getAttribute("aria-activedescendant");
        }
        if (option.id && activeId !== option.id) {
          throw new Error(`Recruiter ${key} typeahead DOM contract mismatch`);
        }
        pressRecruiterKey(currentControl, "Enter");
      } else {
        option.click();
      }
      if (optionPostcondition) {
        let selected = [];
        let inputHasValue = false;
        for (let settleAttempt = 0; settleAttempt < 5; settleAttempt += 1) {
          await wait(100);
          const postScope = recruiterFacetScope(key);
          selected = recruiterSelectedFilter(optionPostcondition, postScope);
          if (optionRequiresEmptyInput) {
            const postInputs = [...postScope.querySelectorAll("input,textarea")].filter((node) =>
              (spec.placeholders || [spec.placeholder]).includes(node.getAttribute("placeholder")) ||
              (spec.ariaLabels || []).some((label) => recruiterAccessibleName(node) === recruiterText(label))
            ).filter((node) => visibleElement(node));
            inputHasValue = postInputs.some((node) => Boolean(node.value));
          }
          if (selected.length > 1) throw new Error(`Recruiter ${key} selected filter contract mismatch`);
          if (selected.length === 1 && !inputHasValue) break;
        }
        if (selected.length !== 1 || inputHasValue) throw new Error(`Recruiter ${key} option contract mismatch`);
      }
    }
    if (currentDiagnostic) diagnostics.push({
      ...currentDiagnostic,
      status: "completed",
      phase: "unknown",
      code: "completed",
      contract_version: RECRUITER_DOM_CONTRACT_VERSION,
      page_kind: pageKind(),
      issues: [],
    });
    } catch (error) {
      throw recruiterContractFailure(
        error,
        diagnostics,
        key,
        currentDiagnostic?.value || values[0] || "",
        currentDiagnostic?.value_index ?? 0,
      );
    }
  }

  function recruiterExperienceRange(value) {
    const values = recruiterFilterValues("years_of_experience", value);
    if (values.length !== 1) throw new Error("Recruiter years_of_experience filter contract mismatch");
    const match = values[0].match(/^(\d{1,2})(?:\s*(?:-|–|—|至|to)\s*(\d{1,2}))?\s*(?:years?|年)?$/i);
    if (!match) throw new Error("Recruiter years_of_experience filter contract mismatch");
    const from = Number(match[1]);
    const to = Number(match[2] ?? match[1]);
    if (from > to || to > 80) throw new Error("Recruiter years_of_experience filter contract mismatch");
    return { from, to };
  }

  function recruiterRangeControls() {
    const scope = recruiterFacetScope("years_of_experience");
    const roots = scope === document ? [document] : [scope];
    const find = (selector) => [...new Set(roots.flatMap((root) =>
      [...root.querySelectorAll(selector)].filter((node) => visibleElement(node))))];
    const from = find("input[data-test-from-field]");
    const to = find("input[data-test-to-field]");
    const submit = find("button[data-test-submit]");
    if (from.length > 1 || to.length > 1 || submit.length > 1) {
      throw new Error("Recruiter years_of_experience range contract mismatch");
    }
    return from.length === 1 && to.length === 1 && submit.length === 1
      ? { scope, from: from[0], to: to[0], submit: submit[0] }
      : null;
  }

  async function recruiterYearsControls() {
    recruiterLocale();
    for (let attempt = 0; attempt < 8; attempt += 1) {
      const controls = recruiterRangeControls();
      if (controls) return controls;
      const expanders = [...document.querySelectorAll("button,[role='button']")].filter((node) =>
        visibleElement(node) && [
          recruiterText(node.getAttribute("aria-label")),
          recruiterText(node.textContent),
        ].some((label) => ["经验年限", "工作年限", "教育及工作经历"].includes(label))
      );
      if (expanders.length > 1) throw new Error("Recruiter years_of_experience range contract mismatch");
      if (expanders.length === 1) expanders[0].click();
      if (attempt < 7) await wait(300);
    }
    throw new Error("Recruiter years_of_experience range contract mismatch");
  }

  async function applyRecruiterExperienceRange(value) {
    const { from, to } = recruiterExperienceRange(value);
    let controls = await recruiterYearsControls();
    const fromValue = String(from);
    const toValue = String(to);
    if (controls.from.value !== fromValue) {
      setRecruiterControlValue(controls.from, fromValue);
      controls.from.dispatchEvent(new Event("change", { bubbles: true }));
      controls.from.blur();
      // Recruiter may replace the whole range facet after the first change.
      // Resolve live controls before touching the second field.
      controls = await recruiterYearsControls();
    }
    if (!controls.from.isConnected || !controls.to.isConnected || !controls.submit.isConnected) {
      controls = await recruiterYearsControls();
    }
    if (controls.to.value !== toValue) {
      setRecruiterControlValue(controls.to, toValue);
      controls.to.dispatchEvent(new Event("change", { bubbles: true }));
      controls.to.blur();
    }
    controls = await recruiterYearsControls();
    for (let attempt = 0; attempt < 10 && controls.submit.disabled; attempt += 1) await wait(100);
    if (controls.submit.disabled) throw new Error("Recruiter years_of_experience submit contract mismatch");
    controls.submit.click();
    for (let attempt = 0; attempt < 8; attempt += 1) {
      await wait(150);
      const current = recruiterRangeControls();
      if (current && current.from.value === fromValue && current.to.value === toValue) return;
      const summary = recruiterText((current || controls).scope.textContent);
      if (summary.includes(fromValue) && summary.includes(toValue)) return;
    }
    throw new Error("Recruiter years_of_experience postcondition mismatch");
  }

  async function recruiterApplyFilters(filters, diagnostics = []) {
    if (!filters || typeof filters !== "object" || Array.isArray(filters) ||
        Object.keys(filters).some((key) => !(key in RECRUITER_FILTERS) && !RECRUITER_UNAVAILABLE_FILTERS.has(key))) {
      throw new Error("Recruiter filter contract mismatch");
    }
    const applied = {};
    const unapplied = [];
    let firstContractError = null;
    for (const key of Object.keys(filters)) {
      const value = filters[key];
      if (value == null || value === "" || (Array.isArray(value) && !value.length)) continue;
      // These unsupported filters are excluded from new compiled plans.
      // Ignore them here so retries of older persisted plans do not reopen
      // DOM paths that the current Recruiter search shell does not expose.
      if (["profile_language", "years_of_experience"].includes(key)) continue;
      const values = recruiterFilterValues(key, value);
      // The current Recruiter search shell may not expose a language facet at
      // all. Treat that deterministic capability gap as an unapplied filter
      // instead of retrying the same impossible DOM contract. Keep the
      // strict typeahead contract whenever the facet is present (as in the
      // supported page build and fixtures).
      if (key === "profile_language" && recruiterFacetScope(key) === document) {
        for (let valueIndex = 0; valueIndex < values.length; valueIndex += 1) {
          diagnostics.push({
            filter: key,
            value_index: valueIndex,
            value: values[valueIndex],
            status: "unsupported",
            phase: "capability",
            code: "unsupported_filter",
            contract_version: RECRUITER_DOM_CONTRACT_VERSION,
            page_kind: pageKind(),
            issues: ["facet_missing", "advanced_search_required"],
          });
        }
        unapplied.push(key);
        continue;
      }
      if (key === "years_of_experience") {
        try {
          await applyRecruiterExperienceRange(value);
        } catch (error) {
          const failed = recruiterContractFailure(error, diagnostics, key, values[0] || String(value), 0);
          firstContractError ||= failed;
          continue;
        }
        diagnostics.push({
          filter: key,
          value_index: 0,
          value: values[0] || String(value),
          status: "completed",
          phase: "unknown",
          code: "completed",
          contract_version: RECRUITER_DOM_CONTRACT_VERSION,
          page_kind: pageKind(),
          issues: [],
        });
        applied[key] = value;
        continue;
      }
      try {
        await applyRecruiterFilter(key, value, diagnostics);
      } catch (error) {
        // Keep checking independent filters so one missing facet does not
        // hide later contract failures in the same browser step.
        firstContractError ||= error;
        continue;
      }
      applied[key] = value;
    }
    if (firstContractError) {
      firstContractError.contractDiagnostics = diagnostics.slice(0, 50);
      throw firstContractError;
    }
    return { applied, unapplied };
  }

  function recruiterFirstResultLink() {
    return [...recruiterResultRoot().querySelectorAll("a[href]")].find((node) => {
      if (!visibleElement(node, true)) return false;
      try {
        const target = new URL(node.getAttribute("href") || "", location.origin);
        return target.origin === location.origin && !target.hash && RECRUITER_RESULT_PROFILE.test(target.pathname);
      } catch (_error) {
        return false;
      }
    });
  }

  function recruiterSearchContextSignature() {
    const url = new URL(location.href);
    const requestId = url.searchParams.get("searchRequestId") || "";
    const contextId = url.searchParams.get("searchContextId") || "";
    const historyId = url.searchParams.get("searchHistoryId") || "";
    return requestId ? `${contextId}:${historyId}:${requestId}` : "";
  }

  function recruiterSearchHasExplicitEmptyState(scope = document) {
    if (resultCount(scope) === 0) return true;
    const text = [...scope.querySelectorAll(
      "[role='status'],[data-test-search-no-results],[data-test-no-results],[data-test-empty-state]"
    )]
      .filter((node) => visibleElement(node))
      .map((node) => (node.textContent || "").normalize("NFKC").trim())
      .join(" ");
    return /没有找到|未找到|无结果|没有结果|no results|0 results/i.test(text);
  }

  async function recruiterFirstResult() {
    let stableMarker = "";
    let stableRounds = 0;
    let link = null;
    for (let attempt = 0; attempt < 30; attempt += 1) {
      if (challengeDetected()) {
        throw Object.assign(new Error("LinkedIn 要求安全验证"), { pauseReason: "challenge_detected" });
      }
      if (pageKind() !== "recruiter_search") {
        throw Object.assign(new Error("Recruiter 搜索结果页已变化"), { pauseReason: "page_changed" });
      }
      link = recruiterFirstResultLink();
      if (!link) {
        if (recruiterSearchHasExplicitEmptyState()) return false;
        stableMarker = "";
        stableRounds = 0;
        await wait(300);
        continue;
      }
      const currentUrl = new URL(link.getAttribute("href") || "", location.origin).pathname;
      const searchContext = recruiterSearchContextSignature();
      const marker = searchContext || currentUrl;
      stableRounds = marker && marker === stableMarker ? stableRounds + 1 : 1;
      stableMarker = marker;
      if (stableRounds >= 2 && recruiterSearchResultState({ allowTransient: true }).ready) break;
      await wait(300);
    }
    if (!link || stableRounds < 2 || !link.isConnected || !visibleElement(link, true)) {
      throw new Error("Recruiter 搜索结果尚未稳定，请重试");
    }
    link.click();
    for (let attempt = 0; attempt < 150; attempt += 1) {
      if (challengeDetected()) throw Object.assign(new Error("LinkedIn 要求安全验证"), { pauseReason: "challenge_detected" });
      if (RECRUITER_PRIVATE_PROFILE_ROUTE.test(location.pathname)) return true;
      await wait(100);
    }
    throw new Error("Recruiter first result navigation contract mismatch");
  }

  function contactInfoTarget() {
    for (const link of document.querySelectorAll("main a[href]")) {
      if (!visibleElement(link)) continue;
      const target = cleanContactUrl(link.getAttribute("href") || "");
      if (target) return target;
    }
    return null;
  }

  function validEmail(value) {
    if (typeof value !== "string" || value.length > 254 || /[\s\u0000-\u001f\u007f]/.test(value)) return false;
    const at = value.lastIndexOf("@");
    if (at < 1 || at > 64 || value.indexOf("@") !== at) return false;
    const local = value.slice(0, at);
    const domain = value.slice(at + 1);
    if (local.startsWith(".") || local.endsWith(".") || local.includes("..") ||
        !/^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+$/.test(local)) return false;
    const labels = domain.split(".");
    return labels.length > 1 && labels.every((label) =>
      /^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$/.test(label));
  }

  function visibleContactEmail() {
    for (const link of document.querySelectorAll("main a[href]")) {
      if (!visibleElement(link)) continue;
      const href = link.getAttribute("href") || "";
      if (!href.toLowerCase().startsWith("mailto:")) continue;
      try {
        const email = decodeURIComponent(href.slice(7).split("?", 1)[0]).trim();
        if (validEmail(email)) return email;
      } catch (_error) {}
    }
    return null;
  }

  function sanitizeDom() {
    const clone = document.documentElement.cloneNode(true);
    const ownProfile = profileLinkHint();
    const originals = [...document.documentElement.querySelectorAll("*")];
    const copies = [...clone.querySelectorAll("*")];
    originals.forEach((node, index) => {
      const style = getComputedStyle(node);
      if (style.display === "none" || style.visibility === "hidden" || style.contentVisibility === "hidden" || style.opacity === "0") {
        copies[index]?.remove();
      }
    });
    if (ownProfile) {
      clone.querySelectorAll("header a[href],nav a[href],[role='menu'] a[href]").forEach((node) => {
        const value = cleanPublicUrl(node.getAttribute("href") || "");
        if (value?.toLowerCase() === ownProfile.toLowerCase()) node.remove();
      });
    }
    clone.querySelectorAll(
      "script,style,noscript,template,iframe,object,embed,canvas,svg,input,textarea,select," +
      "[hidden],[aria-hidden='true'],[inert],[style*='display:none'],[style*='display: none']," +
      "[style*='visibility:hidden'],[style*='visibility: hidden']"
    ).forEach((node) => node.remove());
    clone.querySelectorAll("*").forEach((node) => {
      const marker = [node.id, node.getAttribute("name"), node.getAttribute("autocomplete")]
        .filter(Boolean).join(" ");
      if (/(?:pass|token|secret|csrf|xsrf|otp|mfa|auth|session|cookie|api.?key)/i.test(marker)) {
        node.remove();
        return;
      }
      for (const attribute of [...node.attributes]) {
        const name = attribute.name.toLowerCase();
        if (name.startsWith("on") || name.startsWith("data-") ||
            ["value", "nonce", "integrity", "crossorigin", "srcdoc"].includes(name)) {
          node.removeAttribute(attribute.name);
          continue;
        }
        if (["href", "src", "action"].includes(name)) {
          try {
            const url = new URL(attribute.value, location.origin);
            if (!["https:", "http:"].includes(url.protocol)) node.removeAttribute(attribute.name);
            else {
              url.username = "";
              url.password = "";
              url.search = "";
              url.hash = "";
              node.setAttribute(attribute.name, url.toString());
            }
          } catch (_error) {
            node.removeAttribute(attribute.name);
          }
        }
      }
    });
    return clone.outerHTML.slice(0, 5_000_000);
  }

  function accountHint() {
    // LinkedIn often hides the account link until the "Me" menu opens. The
    // avatar remains stable while that menu mounts; only its path is used
    // and the worker hashes it before sending an account fingerprint.
    const avatar = document.querySelector(
      "header button[aria-expanded] img[src*='media.licdn.com/'],nav button[aria-expanded] img[src*='media.licdn.com/']," +
      "img[alt='用户菜单'][src^='https://media.licdn.com/']"
    )?.src || "";
    try {
      const url = new URL(avatar);
      if (url.protocol === "https:" && url.hostname === "media.licdn.com" && url.pathname) {
        return `${url.origin}${url.pathname}`;
      }
    } catch (_error) {}
    return profileLinkHint();
  }

  function profileLinkHint() {
    const link = document.querySelector(
      "header a[href^='/in/'],nav a[href^='/in/'],[role='menu'] a[href*='/in/'],header a[data-control-name*='identity'][href*='/in/']"
    );
    return cleanPublicUrl(link?.href || "") || "";
  }

  function observation() {
    const kind = pageKind();
    const constrainedPage = kind === "public_contact_info" || kind === "public_profile_detail" || kind.startsWith("recruiter_");
    return {
      content_build: EXTENSION_BUILD,
      protocol: EXTENSION_PROTOCOL,
      url: reportedUrl(location.href),
      title: constrainedPage ? "" : document.title.slice(0, 500),
      visible_text: constrainedPage ? "" : (document.body?.innerText || "").slice(0, 500_000),
      visible_dom: constrainedPage ? "" : sanitizeDom(),
      page_kind: kind,
      account_hint: accountHint(),
      challenge: challengeDetected(),
    };
  }

  function text(selector) {
    return (document.querySelector(selector)?.textContent || "").trim();
  }

  function normalizedHeading(value) {
    return String(value || "").normalize("NFKC").replace(/\s+/g, " ").trim().toLowerCase()
      .replace(/\s*(?:[·•]\s*)?\(?\d+\)?(?:\s*(?:项|items?))?\s*$/i, "").trim();
  }

  function sectionKey(value) {
    const normalized = normalizedHeading(value);
    return Object.entries(SECTION_ALIASES).find(([, aliases]) => aliases.has(normalized))?.[0] || null;
  }

  function businessSection(key) {
    for (const heading of document.querySelectorAll(
      "main h2,main h3,main [role='heading'][aria-level='2'],main [role='heading'][aria-level='3']"
    )) {
      const container = heading.closest("section");
      if (container && visibleElement(heading) && visibleElement(container) && sectionKey(heading.textContent || "") === key) {
        return { heading, container };
      }
    }
    return null;
  }

  function sectionBody({ heading, container }) {
    let cursor = heading;
    while (cursor && cursor !== container) {
      const sibling = cursor.nextElementSibling;
      if (sibling && container.contains(sibling)) return sibling;
      cursor = cursor.parentElement;
    }
    return container;
  }

  function blockText(root) {
    if (!visibleElement(root)) return "";
    const clone = root.cloneNode(true);
    const originals = [root, ...root.querySelectorAll("*")];
    const copies = [clone, ...clone.querySelectorAll("*")];
    originals.forEach((node, index) => {
      if (!visibleElement(node)) copies[index]?.remove();
    });
    clone.querySelectorAll(
      "h1,h2,h3,button,[role='button'],script,style,noscript,template,[hidden],[aria-hidden='true']"
    ).forEach((node) => node.remove());
    clone.querySelectorAll("a[href]").forEach((node) => {
      if (cleanProfileDetailUrl(node.getAttribute("href") || "", location.href)) node.remove();
    });
    return (clone.innerText || clone.textContent || "").split(/\n+/)
      .map((line) => line.trim())
      .filter((line) => line && !/^(?:(?:…|\.\.\.)\s*)?(?:更多|查看更多|展开|see more|show more)$|^(?:显示|查看|show)\s*(?:全部|all)\b/i.test(line))
      .join("\n").trim().slice(0, 5000);
  }

  function sectionItems(key, max = 50) {
    const matched = businessSection(key);
    if (!matched) return [];
    const body = sectionBody(matched);
    let roots = [...body.children].filter((node) => blockText(node));
    if (["experience", "education", "projects", "certifications"].includes(key) && roots.length === 1) {
      const nested = [...roots[0].children].filter((node) => blockText(node));
      if (nested.length > 1) roots = nested;
    }
    if (!roots.length) roots = [body];
    const values = roots.map(blockText).filter(Boolean);
    return [...new Set(values)].slice(0, max);
  }

  function sectionText(key) {
    const matched = businessSection(key);
    return matched ? blockText(sectionBody(matched)).slice(0, 10_000) : "";
  }

  function detailTargets() {
    const urls = {};
    const counts = {};
    for (const key of PROFILE_DETAIL_KINDS) {
      const matched = businessSection(key);
      if (!matched) continue;
      const count = sectionCount(matched.heading, key);
      for (const link of matched.container.querySelectorAll("a[href]")) {
        if (!visibleElement(link)) continue;
        const detail = cleanProfileDetailUrl(link.getAttribute("href") || "", location.href);
        if (detail?.kind !== key) continue;
        urls[key] = detail.url;
        if (Number.isInteger(count) && count > 0 && count <= (key === "skills" ? 100 : 50)) counts[key] = count;
        break;
      }
    }
    return { urls, counts };
  }

  function sectionCount(heading, key) {
    const match = String(heading?.textContent || "").normalize("NFKC")
      .match(/\(?([0-9]+)\)?(?:\s*(?:项|种|items?))?\s*$/i);
    const count = Number(match?.[1] || 0);
    return Number.isSafeInteger(count) && count > 0 ? count : 0;
  }

  function profile() {
    const shellHeading = /^(?:people also viewed|about this profile|更多职业档案推荐|浏览高级档案|猜您认识|您可能喜欢|关注|广告选项|不再显示此内容)$/i;
    const fallbackName = [...document.querySelectorAll("main h1, main h2, main h3")]
      .map((node) => (node.textContent || "").trim())
      .find((value) => value && !sectionKey(value) && !shellHeading.test(value)) || "";
    return {
      name: text("main [data-anonymize='person-name']") || text("main .text-heading-xlarge") || text("main h1") || fallbackName,
      headline: text("main .text-body-medium") || text("main [data-generated-suggestion-target]"),
      location: text("main .text-body-small.inline") || text("main .text-body-small"),
      about: sectionText("about"),
      linkedin_url: cleanPublicUrl(location.href) || "",
      experience: sectionItems("experience"),
      education: sectionItems("education"),
      skills: sectionItems("skills", 100),
      languages: sectionItems("languages"),
      certifications: sectionItems("certifications"),
      projects: sectionItems("projects"),
    };
  }

  function wait(milliseconds) {
    return new Promise((resolve) => setTimeout(resolve, milliseconds));
  }

  function mergeProfile(target, value) {
    for (const key of ["name", "headline", "location", "about", "linkedin_url"]) {
      if (!target[key] && value[key]) target[key] = value[key];
    }
    for (const key of ["experience", "education", "skills", "languages", "certifications", "projects"]) {
      for (const item of value[key] || []) if (!target[key].includes(item)) target[key].push(item);
    }
    return target;
  }

  function scrollRoot() {
    const main = document.querySelector("main");
    const style = main ? getComputedStyle(main) : null;
    if (main && main.scrollHeight > main.clientHeight + 1 && /^(?:auto|scroll)$/.test(style?.overflowY || "")) return main;
    return document.scrollingElement || document.documentElement;
  }

  function scrollState(root) {
    const documentRoot = root === document.scrollingElement || root === document.documentElement || root === document.body;
    return {
      top: documentRoot ? scrollY : root.scrollTop,
      height: root.scrollHeight,
      client: documentRoot ? innerHeight : root.clientHeight,
    };
  }

  function setScrollTop(root, top) {
    const documentRoot = root === document.scrollingElement || root === document.documentElement || root === document.body;
    if (documentRoot) scrollTo({ top, behavior: "auto" });
    else root.scrollTo({ top, behavior: "auto" });
  }

  function assertProfilePage(initialUrl) {
    if (challengeDetected()) {
      const error = new Error("LinkedIn 要求安全验证");
      error.pauseReason = "challenge_detected";
      throw error;
    }
    if (cleanPublicUrl(location.href) !== initialUrl) {
      const error = new Error("读取期间页面已变化");
      error.pauseReason = "page_changed";
      throw error;
    }
  }

  function inlineMoreControls() {
    const controls = [];
    for (const key of ["about", "experience", "education", "projects", "certifications"]) {
      const matched = businessSection(key);
      if (!matched) continue;
      for (const node of sectionBody(matched).querySelectorAll("button,[role='button']")) {
        if (!visibleElement(node) || node.closest("a[href]") || node.hasAttribute("href") ||
            node.hasAttribute("aria-haspopup") || node.getAttribute("aria-expanded") === "true") continue;
        const labels = [node.innerText || node.textContent || "", node.getAttribute("aria-label") || ""]
          .map((value) => value.normalize("NFKC").replace(/\s+/g, " ").trim());
        if (!labels.some((label) => /^(?:(?:…|\.\.\.)\s*)?(?:更多|查看更多|展开|see more|show more)$/i.test(label))) continue;
        controls.push(node);
      }
    }
    return controls;
  }

  async function expandInlineMore(clicked) {
    let count = 0;
    for (const node of inlineMoreControls()) {
        if (clicked.has(node)) continue;
        clicked.add(node);
        node.click();
        count += 1;
    }
    if (count) await wait(100);
  }

  async function prepareProfilePage() {
    if (pageKind() !== "public_profile") throw new Error("公开候选人页无效");
    const initialUrl = cleanPublicUrl(location.href);
    const root = scrollRoot();
    const initialTop = scrollState(root).top;
    const initialProfile = profile();
    const aggregate = {
      name: initialProfile.name, headline: initialProfile.headline, location: initialProfile.location,
      about: "", linkedin_url: initialProfile.linkedin_url,
      experience: [], education: [], skills: [], languages: [], certifications: [], projects: [],
    };
    const clicked = new WeakSet();
    let stableBottomRounds = 0;
    let completed = false;
    try {
      for (let attempt = 0; attempt < 40; attempt += 1) {
        assertProfilePage(initialUrl);
        await expandInlineMore(clicked);
        mergeProfile(aggregate, profile());
        const before = scrollState(root);
        const atBottom = before.top + before.client >= before.height - 10;
        if (!atBottom) setScrollTop(root, Math.min(before.height, before.top + Math.max(before.client * 0.8, 300)));
        await wait(300);
        assertProfilePage(initialUrl);
        await expandInlineMore(clicked);
        mergeProfile(aggregate, profile());
        const after = scrollState(root);
        stableBottomRounds = atBottom && after.top + after.client >= after.height - 10 && after.height === before.height
          ? stableBottomRounds + 1 : 0;
        if (stableBottomRounds >= 2) { completed = true; break; }
      }
      if (!completed) throw new Error("候选人档案未完整加载，请重试");
      await expandInlineMore(clicked);
      if (inlineMoreControls().length) throw new Error("候选人档案正文仍有未展开内容，请重试");
      mergeProfile(aggregate, profile());
      for (const key of ["experience", "education", "projects", "certifications"]) {
        const matched = businessSection(key);
        const count = matched ? sectionCount(matched.heading, key) : 0;
        if (count && aggregate[key].length !== count) throw new Error(`${key} 数量与栏目不一致`);
      }
      const details = detailTargets();
      const result = strictTypedProfile(aggregate);
      if (!result.name) throw new Error("候选人姓名尚未加载完成");
      return { profile: result, detail_urls: details.urls, detail_counts: details.counts };
    } finally {
      setScrollTop(root, initialTop);
      await wait(150);
    }
  }

  async function preparedProfileResponse() {
    try {
      return {
        status: "completed",
        data: { ...await prepareProfilePage(), extracted_at: new Date().toISOString() },
      };
    } catch (error) {
      return {
        status: error.pauseReason ? "paused" : "failed",
        data: {},
        pause_reason: error.pauseReason,
        error: error.message || "页面动作失败",
      };
    }
  }

  function detailItems(kind) {
    const root = document.querySelector("main section[aria-label='主要内容']") || document.querySelector("main");
    if (!root) return [];
    const values = [...root.querySelectorAll("p")]
      .filter((node) => !node.closest("nav,[role='list']"))
      .map((node) => (node.innerText || node.textContent || "").trim())
      .filter(Boolean);
    if (values.length && SECTION_ALIASES[kind]?.has(normalizedHeading(values[0]))) values.shift();
    if (kind === "skills") return [...new Set(values)].slice(0, 100);
    const grouped = [];
    for (let index = 0; index < values.length; index += 2) grouped.push(values.slice(index, index + 2).join("\n"));
    return [...new Set(grouped.filter(Boolean))].slice(0, 50);
  }

  async function prepareProfileDetail(kind, profileUrl, expectedCount) {
    const initial = cleanProfileDetailUrl(location.href, profileUrl);
    if (!initial || initial.kind !== kind || pageKind() !== "public_profile_detail") throw new Error("候选人详情页无效");
    const root = scrollRoot();
    const initialTop = scrollState(root).top;
    const items = [];
    let stableBottomRounds = 0;
    let completed = false;
    try {
      for (let attempt = 0; attempt < 40; attempt += 1) {
        if (challengeDetected()) throw Object.assign(new Error("LinkedIn 要求安全验证"), { pauseReason: "challenge_detected" });
        const current = cleanProfileDetailUrl(location.href, profileUrl);
        if (!current || current.url !== initial.url) throw Object.assign(new Error("读取期间页面已变化"), { pauseReason: "page_changed" });
        for (const item of detailItems(kind)) if (!items.includes(item)) items.push(item);
        const before = scrollState(root);
        const atBottom = before.top + before.client >= before.height - 10;
        if (!atBottom) setScrollTop(root, Math.min(before.height, before.top + Math.max(before.client * 0.8, 300)));
        await wait(300);
        const after = scrollState(root);
        stableBottomRounds = atBottom && after.top + after.client >= after.height - 10 && after.height === before.height
          ? stableBottomRounds + 1 : 0;
        if (stableBottomRounds >= 2) { completed = true; break; }
      }
      for (const item of detailItems(kind)) if (!items.includes(item)) items.push(item);
      if (!completed) throw new Error("候选人详情未完整加载，请重试");
      if (expectedCount && items.length !== expectedCount) throw new Error(`${kind} 详情数量与主页不一致`);
      return items;
    } finally {
      setScrollTop(root, initialTop);
      await wait(150);
    }
  }

  function strictTypedProfile(value) {
    if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("候选人字段无效");
    const limits = {
      name: 500, headline: 1000, location: 500, about: 10_000, linkedin_url: 2048,
    };
    const listLimits = {
      experience: [50, 5000], education: [50, 5000], skills: [100, 200],
      languages: [50, 200], certifications: [50, 5000], projects: [50, 5000],
    };
    const allowed = new Set([...Object.keys(limits), ...Object.keys(listLimits)]);
    if (Object.keys(value).some((key) => !allowed.has(key))) throw new Error("候选人包含未批准字段");
    const result = {};
    for (const [key, max] of Object.entries(limits)) {
      const item = value[key] ?? "";
      if (typeof item !== "string" || item.length > max || (key === "name" && !item.trim())) {
        throw new Error(`${key} 无效`);
      }
      result[key] = item.trim();
    }
    if (result.linkedin_url) {
      const publicUrl = cleanPublicUrl(result.linkedin_url);
      if (!publicUrl) throw new Error("LinkedIn 公开主页无效");
      result.linkedin_url = publicUrl;
    }
    for (const [key, [count, max]] of Object.entries(listLimits)) {
      const items = value[key] ?? [];
      if (!Array.isArray(items) || items.length > count || items.some((item) => typeof item !== "string" || item.length > max)) {
        throw new Error(`${key} 无效`);
      }
      result[key] = items.map((item) => item.trim()).filter(Boolean);
    }
    return result;
  }

  function observedRecruiterString(value, field) {
    if (value == null) return "";
    if (typeof value !== "string") throw new Error(`Recruiter ${field} schema drift`);
    return value.trim();
  }

  function observedRecruiterArray(value, field) {
    if (value == null) return [];
    if (!Array.isArray(value)) throw new Error(`Recruiter ${field} schema drift`);
    return value;
  }

  function observedRecruiterDate(value, field) {
    if (value == null || value === "") return "";
    if (typeof value === "string") return value.trim();
    if (!value || typeof value !== "object" || Array.isArray(value)) {
      throw new Error(`Recruiter ${field} schema drift`);
    }
    const parts = [];
    for (const key of ["year", "month", "day"]) {
      if (value[key] == null) continue;
      if (!Number.isInteger(value[key]) || value[key] < 1) throw new Error(`Recruiter ${field} schema drift`);
      parts.push(String(value[key]).padStart(key === "year" ? 4 : 2, "0"));
    }
    return parts.join("-");
  }

  function observedRecruiterLocation(value) {
    if (value == null) return "";
    if (!value || typeof value !== "object" || Array.isArray(value)) {
      throw new Error("Recruiter location schema drift");
    }
    const displayName = observedRecruiterString(value.displayName, "location.displayName");
    const countryCode = observedRecruiterString(value.countryCode, "location.countryCode");
    return [displayName, countryCode].filter(Boolean).join(" · ");
  }

  function observedRecruiterRecord(value, field, fields) {
    if (!value || typeof value !== "object" || Array.isArray(value)) {
      throw new Error(`Recruiter ${field} schema drift`);
    }
    const lines = [];
    const heading = fields.heading.map((key) => observedRecruiterString(value[key], `${field}.${key}`)).filter(Boolean).join(" | ");
    const dates = [
      observedRecruiterDate(value.startDateOn, `${field}.startDateOn`),
      observedRecruiterDate(value.endDateOn, `${field}.endDateOn`),
    ].filter(Boolean).join(" - ");
    const location = observedRecruiterLocation(value.location);
    const meta = [dates, location].filter(Boolean).join(" | ");
    const description = observedRecruiterString(value.description, `${field}.description`);
    if (heading) lines.push(heading);
    if (meta) lines.push(meta);
    if (description) lines.push(description);
    return lines.join("\n");
  }

  function projectObservedRecruiterProfile(payload) {
    if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
      throw new Error("Recruiter profile schema drift");
    }
    const currentPositions = observedRecruiterArray(payload.currentPositions, "currentPositions");
    const groupedWorkExperience = observedRecruiterArray(payload.groupedWorkExperience, "groupedWorkExperience");
    if (!groupedWorkExperience.length) throw new Error("Recruiter groupedWorkExperience schema drift");
    const historicalPositions = groupedWorkExperience.flatMap((item) => {
      if (!item || typeof item !== "object" || Array.isArray(item)) {
        throw new Error("Recruiter groupedWorkExperience[] schema drift");
      }
      const positions = observedRecruiterArray(item.positions, "groupedWorkExperience[].positions");
      if (!positions.length) throw new Error("Recruiter groupedWorkExperience[].positions schema drift");
      return positions;
    });
    const experience = [...new Set([...currentPositions, ...historicalPositions]
      .map((item) => observedRecruiterRecord(item, "positions[]", { heading: ["title", "companyName"] }))
      .filter(Boolean))];
    const education = observedRecruiterArray(payload.educations, "educations")
      .map((item) => observedRecruiterRecord(item, "educations[]", { heading: ["schoolName", "school", "degreeName", "fieldOfStudy"] }))
      .filter(Boolean);
    const skills = observedRecruiterArray(payload.profileSkills, "profileSkills").map((item) => {
      if (!item || typeof item !== "object" || Array.isArray(item)) throw new Error("Recruiter profileSkills schema drift");
      return observedRecruiterString(item.name, "profileSkills[].name");
    }).filter(Boolean);
    const accomplishments = payload.accomplishments;
    if (!accomplishments || typeof accomplishments !== "object" || Array.isArray(accomplishments)) {
      throw new Error("Recruiter accomplishments schema drift");
    }
    for (const key of ["languages", "certifications", "projects"]) {
      const items = observedRecruiterArray(accomplishments[key], `accomplishments.${key}`);
      if (items.length) throw new Error(`Recruiter accomplishments.${key} schema drift`);
    }
    const firstName = observedRecruiterString(payload.firstName, "firstName");
    const lastName = observedRecruiterString(payload.lastName, "lastName");
    return strictTypedProfile({
      name: [firstName, lastName].filter(Boolean).join(" "),
      headline: observedRecruiterString(payload.headline, "headline"),
      location: observedRecruiterLocation(payload.location),
      about: observedRecruiterString(payload.summary, "summary"),
      linkedin_url: "",
      experience,
      education,
      skills,
      languages: [],
      certifications: [],
      projects: [],
    });
  }

  function retryAfterSeconds(value) {
    if (!value) return null;
    const numeric = Number(value);
    const seconds = Number.isFinite(numeric)
      ? Math.ceil(numeric)
      : Math.ceil((Date.parse(value) - Date.now()) / 1000);
    return Number.isInteger(seconds) && seconds >= 1 && seconds <= 86_400
      ? seconds
      : null;
  }

  function retryablePrivateError(message, errorCategory) {
    return Object.assign(new Error(message), {
      pauseReason: "contract_mismatch",
      retryable: true,
      errorCategory,
    });
  }

  function privateErrorCategory(error) {
    const value = error?.errorCategory;
    return typeof value === "string" &&
      (PRIVATE_ERROR_CATEGORIES.has(value) || /^http_5\d\d$/.test(value))
      ? value
      : undefined;
  }

  async function sha256(value) {
    const bytes = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
    return [...new Uint8Array(bytes)].map((item) => item.toString(16).padStart(2, "0")).join("");
  }

  async function fixedPrivateFetch(message) {
    const fields = new Set([
      "type", "version", "run_id", "step_id", "command_id", "surface", "operation",
      "headers", "protocol_headers", "body", "profile_ref_hash",
    ]);
    if (Object.keys(message).some((key) => !fields.has(key))) throw new Error("私有读取协议字段无效");
    if (message.run_id !== monitoredRunId) throw new Error("私有读取任务未绑定");
    const contract = PRIVATE_CONTRACTS[message.surface];
    const adapter = contract?.operations?.[message.operation];
    if (!contract?.enabled || !adapter) throw new Error("Recruiter/Lite contract 尚未启用");
    if (challengeDetected()) throw Object.assign(new Error("LinkedIn 要求安全验证"), { pauseReason: "challenge_detected" });
    if (!message.headers || typeof message.headers !== "object" || Array.isArray(message.headers)) throw new Error("请求头无效");
    const allowedHeaders = new Set(contract.headerNames);
    const headers = {};
    for (const [name, value] of Object.entries(message.headers)) {
      const lower = name.toLowerCase();
      const csrfException = adapter.allowCsrf && lower === "csrf-token";
      if (!allowedHeaders.has(lower) || (FORBIDDEN_HEADERS.test(lower) && !csrfException) || typeof value !== "string" || !value) {
        throw new Error("请求头越界");
      }
      headers[lower] = value;
    }
    if (Object.keys(headers).length !== allowedHeaders.size) throw new Error("请求头 contract 不完整");
    if (!message.protocol_headers || typeof message.protocol_headers !== "object" || Array.isArray(message.protocol_headers) ||
        Object.keys(message.protocol_headers).sort().join() !== "content-type,x-http-method-override" ||
        !/^application\/json(?:\s*;|$)/i.test(message.protocol_headers["content-type"]) ||
        String(message.protocol_headers["x-http-method-override"]).trim().toUpperCase() !== "GET") {
      throw new Error("固定协议头 contract 不匹配");
    }
    headers["content-type"] = message.protocol_headers["content-type"];
    headers["x-http-method-override"] = message.protocol_headers["x-http-method-override"];
    if (typeof message.body !== "string" || !message.body || message.body.length > 65_536 ||
        !/^[0-9a-f]{64}$/.test(message.profile_ref_hash || "")) throw new Error("请求 body contract 不匹配");
    let body;
    try { body = JSON.parse(message.body); } catch (_error) { throw new Error("请求 body contract 不匹配"); }
    if (!body || typeof body !== "object" || Array.isArray(body) ||
        Object.keys(body).sort().join() !== "altkey,decoration") throw new Error("请求 body contract 不匹配");

    const initialUrl = canonicalNavigationUrl(location.href);
    const initialKind = pageKind();
    const initialAccountHint = accountHint();
    if (!initialAccountHint) throw Object.assign(new Error("无法确认当前 LinkedIn 账号"), { pauseReason: "account_changed" });
    const controller = new AbortController();
    activeFetchController?.abort();
    activeFetchController = controller;
    let timedOut = false;
    const timeout = setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, 20_000);
    try {
      const route = location.pathname.match(/^\/talent\/(?:search\/)?profile\/([^/?#]+)\/?$/);
      if (!route || await sha256(route[1]) !== message.profile_ref_hash) {
        throw Object.assign(new Error("Recruiter profile 引用不匹配"), { pauseReason: "page_changed" });
      }
      const target = new URL(`${RECRUITER_PROFILE_ENDPOINT}${route[1]}`, location.origin);
      if (target.origin !== location.origin || !["GET", "POST"].includes(adapter.method)) {
        throw Object.assign(new Error("私有请求 contract 越界"), { pauseReason: "contract_mismatch" });
      }
      let response;
      try {
        response = await fetch(target, {
          method: adapter.method,
          headers,
          body: message.body,
          credentials: "same-origin",
          cache: "no-store",
          redirect: "error",
          signal: controller.signal,
        });
      } catch (error) {
        if (timedOut) throw retryablePrivateError("私有请求超时", "network");
        if (error instanceof TypeError) throw retryablePrivateError("私有请求网络失败", "network");
        throw error;
      }
      if (response.status === 401) throw Object.assign(new Error("需要重新登录"), { pauseReason: "auth_required" });
      if (response.status === 403) throw Object.assign(new Error("当前席位无权限"), { pauseReason: "permission_denied" });
      if (response.status === 429) {
        throw Object.assign(new Error("已触发限流"), {
          pauseReason: "rate_limited",
          retryAfterSeconds: retryAfterSeconds(response.headers.get("retry-after")),
        });
      }
      const contentType = response.headers.get("content-type") || "";
      if (/^text\/html(?:\s*;|$)/i.test(contentType)) {
        throw Object.assign(new Error("LinkedIn 要求安全验证"), { pauseReason: "challenge_detected" });
      }
      if (response.status >= 500 && response.status <= 599) {
        throw retryablePrivateError(`私有服务返回 ${response.status}`, `http_${response.status}`);
      }
      if (!response.ok || !/^application\/(?:[a-z0-9.+-]*\+)?json(?:\s*;|$)/i.test(contentType)) {
        throw Object.assign(new Error("私有响应 contract 不匹配"), { pauseReason: "contract_mismatch" });
      }
      const limit = adapter.kind === "profile" ? 1_048_576 : 2_097_152;
      const reader = response.body?.getReader();
      if (!reader) throw Object.assign(new Error("私有响应不可读"), { pauseReason: "contract_mismatch" });
      const chunks = [];
      let size = 0;
      try {
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          size += value.byteLength;
          if (size > limit) {
            controller.abort();
            throw Object.assign(new Error("私有响应超限"), { pauseReason: "contract_mismatch" });
          }
          chunks.push(value);
        }
      } catch (error) {
        if (error?.pauseReason) throw error;
        if (timedOut) throw retryablePrivateError("私有请求超时", "network");
        if (error instanceof TypeError) throw retryablePrivateError("私有响应网络失败", "network");
        throw error;
      }
      if (challengeDetected()) throw Object.assign(new Error("LinkedIn 要求安全验证"), { pauseReason: "challenge_detected" });
      if (accountHint() !== initialAccountHint) throw Object.assign(new Error("读取期间账号已变化"), { pauseReason: "account_changed" });
      if (canonicalNavigationUrl(location.href) !== initialUrl || pageKind() !== initialKind) throw Object.assign(new Error("读取期间页面已变化"), { pauseReason: "page_changed" });
      const bytes = new Uint8Array(size);
      let offset = 0;
      for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
      const projected = adapter.project(JSON.parse(new TextDecoder().decode(bytes)));
      return adapter.kind === "profile" ? strictTypedProfile(projected) : projected;
    } finally {
      clearTimeout(timeout);
      if (activeFetchController === controller) activeFetchController = null;
    }
  }

  function resultCount(scope = document) {
    const text = scope === document ? (document.body?.innerText || "") : (scope.innerText || scope.textContent || "");
    const match = text.match(/([\d,]+)\s*(?:条\s*)?(?:results?|结果)/i);
    return match ? Number(match[1].replaceAll(",", "")) : null;
  }

  function nextUrl() {
    const controls = [...document.querySelectorAll("main a[href],main button")].filter((node) =>
      /^(next|下一步|下一页)$/i.test((node.getAttribute("aria-label") || node.textContent || "").trim()) &&
      visibleElement(node) && node.getAttribute("aria-disabled") !== "true" && !node.disabled
    );
    if (controls.length !== 1) return null;
    const control = controls[0];
    if (control.tagName.toLowerCase() === "button") {
      const url = new URL(location.href);
      const page = Number(url.searchParams.get("page") || 1);
      if (!Number.isInteger(page) || page < 1 || page >= 99) return null;
      url.searchParams.set("page", String(page + 1));
      return url.toString();
    }
    try {
      const url = new URL(control.href, location.origin);
      if (url.hostname !== "www.linkedin.com" || !PEOPLE_SEARCH.test(url.pathname)) return null;
      return url.toString();
    } catch (_error) {
      return null;
    }
  }

  function collectedUrls(limit) {
    const root = document.querySelector("main") || document;
    const urls = [];
    for (const link of root.querySelectorAll("a[href*='/in/']")) {
      if (link.closest("[hidden],[aria-hidden='true'],[style*='display:none'],[style*='display: none'],[style*='visibility:hidden'],[style*='visibility: hidden']")) continue;
      let current = link;
      let hidden = false;
      while (current && current !== root.parentElement) {
        const style = getComputedStyle(current);
        if (style.display === "none" || style.visibility === "hidden") {
          hidden = true;
          break;
        }
        current = current.parentElement;
      }
      if (hidden) continue;
      const url = cleanPublicUrl(link.href);
      if (url && !urls.includes(url)) urls.push(url);
      if (urls.length >= limit) break;
    }
    return urls;
  }

  function safeFilters(filters) {
    if (!filters || typeof filters !== "object" || Array.isArray(filters)) throw new Error("筛选条件无效");
    const clean = {};
    for (const [key, value] of Object.entries(filters)) {
      if (!FILTER_KEYS.has(key)) throw new Error("未允许的筛选条件");
      if (value === null || (Array.isArray(value) && value.length === 0)) continue;
      const values = Array.isArray(value) ? value : [value];
      if (values.some((item) => typeof item !== "string" || !item.trim() || item.length > 500)) {
        throw new Error("筛选值无效");
      }
      clean[key] = Array.isArray(value) ? values.map((item) => item.trim()) : value.trim();
    }
    return clean;
  }

  function filteredUrl(filters) {
    const url = new URL(location.href);
    const mapping = {
      keywords: "keywords", location: "geoUrn", current_company: "currentCompany",
      past_company: "pastCompany", industry: "industry", school: "schoolFilter",
      title: "titleFreeText", profile_language: "profileLanguage", network: "network", skills: "skills",
    };
    const textKeys = new Set(["keywords", "title"]);
    const applied = {};
    const unapplied = [];
    for (const [key, param] of Object.entries(mapping)) {
      url.searchParams.delete(param);
      const value = filters[key];
      if (value === null || value === undefined || (Array.isArray(value) && value.length === 0) || value === "") continue;
      if (!textKeys.has(key)) {
        unapplied.push(key);
        continue;
      }
      const values = Array.isArray(value) ? value : [value];
      const encoded = values.join(" OR ");
      if (!encoded) continue;
      url.searchParams.set(mapping[key], encoded);
      applied[key] = Array.isArray(value) ? values : encoded;
    }
    return { url: url.toString(), applied, unapplied };
  }

  function validateCommand(message) {
    if (message?.type !== "AGENT_TAB_COMMAND" || message.version !== VERSION) return "协议版本无效";
    const fields = new Set(["type", "version", "run_id", "step_id", "command_id", "lease_id", "agent_role", "action", "arguments"]);
    if (Object.keys(message).some((key) => !fields.has(key))) return "协议字段无效";
    for (const key of ["run_id", "step_id", "command_id", "agent_role", "action"]) {
      if (typeof message[key] !== "string" || !message[key] || message[key].length > 200) return `${key} 无效`;
    }
    if (message.lease_id !== undefined &&
        (typeof message.lease_id !== "string" || !message.lease_id || message.lease_id.length > 200)) {
      return "lease_id 无效";
    }
    if (!ROLE_ACTIONS[message.agent_role]?.has(message.action)) return "角色或动作未授权";
    if (!message.arguments || typeof message.arguments !== "object" || Array.isArray(message.arguments)) return "动作参数无效";
    return null;
  }

  function candidateIndex(args, optional = false) {
    const keys = Object.keys(args);
    if (optional && keys.length === 0) return 0;
    if (keys.length !== 1 || keys[0] !== "candidate_index" ||
        !Number.isInteger(args.candidate_index) || args.candidate_index < 0 || args.candidate_index > 24) {
      throw new Error("候选人序号无效");
    }
    return args.candidate_index;
  }

  async function executeRecruiter(message, kind) {
    const args = message.arguments;
    const expectedKind = ["open_search", "apply_filters", "collect_batch"].includes(message.action)
      ? "recruiter_search"
      : message.action === "open_profile" ? "recruiter_profile" : null;
    if (expectedKind && kind !== expectedKind) {
      return {
        status: "paused",
        data: { page_kind: kind },
        pause_reason: "page_changed",
        error: "Recruiter 页面已变化",
      };
    }
    recruiterLocale();
    if (message.action === "open_search") {
      if (Object.keys(args).join() !== "query" ||
          typeof args.query !== "string" || !args.query.trim() || args.query.length > 500) {
        throw new Error("Recruiter 搜索页或关键词无效");
      }
      const query = args.query.trim();
      let control = await recruiterGlobalSearch();
      if (recruiterSearchAlreadyActive(control, query)) {
        return { status: "completed", data: { reused_search: true } };
      }
      await resetRecruiterSearchState();
      control = await recruiterGlobalSearch();
      setRecruiterControlValue(control, query);
      control.dispatchEvent(new Event("change", { bubbles: true }));
      await submitRecruiterSearch(control, query);
      return { status: "completed", data: {} };
    }
    if (message.action === "apply_filters") {
      if (Object.keys(args).join() !== "filters") throw new Error("Recruiter 筛选参数无效");
      const diagnostics = [];
      const filters = await recruiterApplyFilters(args.filters, diagnostics);
      return {
        status: "completed",
        data: { applied_filters: filters.applied, unapplied_filters: filters.unapplied },
        diagnostics,
      };
    }
    if (message.action === "collect_batch") {
      if (Object.keys(args).join() !== "count" || args.count !== 1) {
        throw new Error("Recruiter 单人采集参数无效");
      }
      return { status: "completed", data: { collected_count: await recruiterFirstResult() ? 1 : 0 } };
    }
    if (message.action === "open_profile") {
      candidateIndex(args, true);
      return { status: "completed", data: {} };
    }
    if (message.action === "import_candidates") {
      if (Object.keys(args).length) throw new Error("入库参数无效");
      return { status: "completed", data: {} };
    }
    throw new Error("Recruiter 动作未授权");
  }

  function execute(message) {
    const kind = pageKind();
    if (challengeDetected()) return { status: "paused", data: { page_kind: kind }, pause_reason: "challenge_detected", error: "LinkedIn 要求安全验证" };
    if (kind === "recruiter_search" || kind === "recruiter_profile") return executeRecruiter(message, kind);
    if (kind === "unsupported") return { status: "paused", data: { page_kind: kind }, pause_reason: "page_changed", error: "已离开允许的公开 LinkedIn 页面" };
    const args = message.arguments;
    if (message.action === "open_search") {
      if (kind !== "people_search" || Object.keys(args).join() !== "query" || typeof args.query !== "string" || !args.query.trim()) throw new Error("公开人才搜索页或关键词无效");
      return { status: "completed", data: { url: location.href, result_count: resultCount(), page: Number(new URL(location.href).searchParams.get("page") || 1) } };
    }
    if (message.action === "apply_filters") {
      if (kind !== "people_search" || Object.keys(args).join() !== "filters") throw new Error("筛选参数无效");
      const filters = safeFilters(args.filters);
      const compiled = filteredUrl(filters);
      return {
        status: "completed",
        data: {
          url: location.href,
          target_url: compiled.url,
          result_count: resultCount(),
          applied_filters: compiled.applied,
          unapplied_filters: compiled.unapplied,
        },
      };
    }
    if (message.action === "next_page") {
      if (kind !== "people_search" || Object.keys(args).length) throw new Error("翻页参数无效");
      const target = nextUrl();
      return { status: "completed", data: { url: location.href, next_url: target, page: Number(new URL(location.href).searchParams.get("page") || 1), has_next: Boolean(target) } };
    }
    if (message.action === "collect_batch") {
      if (kind !== "people_search" || Object.keys(args).join() !== "count" || !Number.isInteger(args.count) || args.count < 1 || args.count > 25) throw new Error("采集人数无效");
      const target = nextUrl();
      const profileUrls = collectedUrls(args.count);
      return { status: "completed", data: { profile_urls: profileUrls, collected_count: profileUrls.length, next_url: target, has_next: Boolean(target) } };
    }
    if (message.action === "open_profile") {
      candidateIndex(args, true);
      if (kind !== "public_profile") throw new Error("公开候选人页无效");
      return { status: "completed", data: { url: cleanPublicUrl(location.href), profile_ref: location.pathname.split("/")[2] } };
    }
    if (message.action === "extract_profile") {
      candidateIndex(args);
      if (kind !== "public_profile") throw new Error("公开候选人页无效");
      return { status: "completed", data: { profile: profile(), extracted_at: new Date().toISOString() } };
    }
    if (message.action === "open_contact_info") {
      candidateIndex(args);
      if (kind === "public_contact_info") {
        return { status: "completed", data: { target_url: cleanContactUrl(location.href) } };
      }
      if (kind !== "public_profile") throw new Error("公开候选人 Contact info 入口无效");
      return { status: "completed", data: { target_url: contactInfoTarget() } };
    }
    if (message.action === "extract_contact_info") {
      candidateIndex(args);
      if (kind !== "public_contact_info") throw new Error("公开候选人 Contact info 页面无效");
      return { status: "completed", data: { email: visibleContactEmail(), source: "linkedin_native" } };
    }
    if (message.action === "import_candidates") {
      if (Object.keys(args).length) throw new Error("入库参数无效");
      return { status: "completed", data: {} };
    }
    throw new Error("未授权的动作");
  }

  chrome.runtime.onMessage.addListener((message, _sender, respond) => {
    if (message?.type === "AGENT_MONITOR_START" && message.version === VERSION) {
      try { startMonitor(message.run_id); respond({ ok: true }); }
      catch (error) { respond({ ok: false, error: error.message }); }
      return;
    }
    if (message?.type === "AGENT_MONITOR_STOP" && message.version === VERSION) {
      stopMonitor();
      respond({ ok: true });
      return;
    }
    if (message?.type === "AGENT_PRIVATE_OPERATION" && message.version === VERSION) {
      fixedPrivateFetch(message)
        .then((data) => respond({ status: "completed", data }))
        .catch((error) => respond({
          status: "paused",
          data: {},
          pause_reason: error.pauseReason || "contract_mismatch",
          retry_after_seconds: error.retryAfterSeconds || undefined,
          error_category: privateErrorCategory(error),
          error: error.message,
        }));
      return true;
    }
    if (message?.type === "AGENT_PAGE_PING" && message.version === VERSION) {
      const kind = pageKind();
      const ready = ["people_search", "public_profile", "public_profile_detail", "public_contact_info", "recruiter_search", "recruiter_profile"].includes(kind);
      respond({
        ok: ready,
        ready,
        content_build: EXTENSION_BUILD,
        protocol: EXTENSION_PROTOCOL,
        page_kind: kind,
      });
      return;
    }
    if (message?.type === "AGENT_RECRUITER_CANDIDATES_REQUEST" && message.version === VERSION) {
      const fields = new Set(["type", "version", "intent", "filter", "value"]);
      const valid = Object.keys(message).every((key) => fields.has(key)) &&
        Object.keys(message).length === fields.size &&
        RECRUITER_RECOVERY_INTENTS.has(message.intent) &&
        RECRUITER_RECOVERY_FILTERS.has(message.filter) &&
        typeof message.value === "string" && message.value.trim() && message.value.length <= 500 &&
        (message.intent !== "submit_search" || message.filter === "keywords");
      if (!valid) {
        respond({ status: "failed", data: {}, error: "Recruiter 候选请求参数无效" });
        return;
      }
      try {
        respond({ status: "completed", data: recruiterRecoveryCandidateSnapshot(
          message.intent, message.filter, message.value.trim(),
        ) });
      } catch (error) {
        respond({ status: "failed", data: {}, error: error.message || "Recruiter 候选项诊断失败" });
      }
      return;
    }
    if (message?.type === "AGENT_RECRUITER_CANDIDATE_ACTION" && message.version === VERSION) {
      const fields = new Set(["type", "version", "snapshot_id", "candidate_id", "action"]);
      const valid = Object.keys(message).every((key) => fields.has(key)) &&
        Object.keys(message).length === fields.size &&
        typeof message.snapshot_id === "string" && message.snapshot_id.length <= 200 &&
        typeof message.candidate_id === "string" && message.candidate_id.length <= 100 &&
        RECRUITER_RECOVERY_ACTIONS.has(message.action);
      if (!valid) {
        respond({ status: "failed", data: {}, error: "Recruiter 候选动作参数无效" });
        return;
      }
      executeRecruiterRecoveryAction(message.snapshot_id, message.candidate_id, message.action)
        .then((data) => respond({ status: "completed", data }))
        .catch((error) => respond({ status: "failed", data: {}, error: error.message || "Recruiter 候选动执行失败" }));
      return true;
    }
    if (message?.type === "AGENT_RECRUITER_POSTCONDITION_REQUEST" && message.version === VERSION) {
      const fields = new Set(["type", "version", "intent", "filter", "value", "snapshot_id", "candidate_id"]);
      const optional = ["snapshot_id", "candidate_id"];
      const valid = Object.keys(message).every((key) => fields.has(key)) &&
        RECRUITER_RECOVERY_INTENTS.has(message.intent) &&
        RECRUITER_RECOVERY_FILTERS.has(message.filter) &&
        typeof message.value === "string" && message.value.trim() && message.value.length <= 500 &&
        optional.every((key) => message[key] === undefined || (typeof message[key] === "string" && message[key].length <= 200)) &&
        (message.intent !== "submit_search" || message.filter === "keywords");
      if (!valid) {
        respond({ status: "failed", data: {}, error: "Recruiter 后置条件参数无效" });
        return;
      }
      try {
        let expectedSelection = message.value.trim();
        if (message.snapshot_id && message.candidate_id) {
          const snapshot = recruiterRecoverySnapshot(message.snapshot_id);
          const record = snapshot.records.find((candidate) => candidate.candidate_id === message.candidate_id);
          if (!record) throw new Error("Recruiter 候选项不存在");
          expectedSelection = record.postcondition_value;
        }
        respond({ status: "completed", data: recruiterRecoveryPostcondition(
          message.intent, message.filter, message.value.trim(), expectedSelection,
        ) });
      } catch (error) {
        respond({ status: "failed", data: {}, error: error.message || "Recruiter 后置条件检查失败" });
      }
      return;
    }
    if (message?.type === "PAGE_OBSERVATION_REQUEST" && message.version === VERSION) {
      respond(observation());
      return;
    }
    if (message?.type === "AGENT_PROFILE_PREPARE" && message.version === VERSION) {
      if (Object.keys(message).sort().join(",") !== "type,version") {
        respond({ status: "failed", data: {}, error: "候选人准备参数无效" });
        return;
      }
      preparedProfileResponse().then(respond).catch((error) => respond({
        status: "failed",
        data: {},
        error: error.message || "候选人档案准备失败",
      }));
      return true;
    }
    if (message?.type === "AGENT_PROFILE_DETAIL_EXTRACT" && message.version === VERSION) {
      const fields = new Set(["type", "version", "detail_kind", "profile_url", "expected_count"]);
      const valid = Object.keys(message).every((key) => fields.has(key)) &&
        PROFILE_DETAIL_KINDS.has(message.detail_kind) && typeof message.profile_url === "string" &&
        Number.isInteger(message.expected_count) && message.expected_count >= 1 &&
        message.expected_count <= (message.detail_kind === "skills" ? 100 : 50) &&
        cleanProfileDetailUrl(location.href, message.profile_url)?.kind === message.detail_kind;
      if (!valid) {
        respond({ status: "failed", data: {}, error: "候选人详情读取参数无效" });
        return;
      }
      prepareProfileDetail(message.detail_kind, message.profile_url, message.expected_count)
        .then((items) => respond({ status: "completed", data: { kind: message.detail_kind, items } }))
        .catch((error) => respond({
          status: error.pauseReason ? "paused" : "failed",
          data: {},
          pause_reason: error.pauseReason,
          error: error.message || "候选人详情读取失败",
        }));
      return true;
    }
    if (message?.type === "capture-linkedin-profile" && message.version === VERSION) {
      if (pageKind() !== "public_profile" || challengeDetected()) {
        respond({ error: "请先打开正常的 LinkedIn 公开个人资料页" });
      } else {
        try {
          respond({
            linkedin_url: cleanPublicUrl(location.href),
            raw_html: sanitizeDom(),
            typed_profile: strictTypedProfile(profile()),
            extracted_at: new Date().toISOString(),
          });
        } catch (error) {
          respond({ error: error.message || "候选人档案尚未加载完成" });
        }
      }
      return;
    }
    if (message?.type !== "AGENT_TAB_COMMAND") return;
    const error = validateCommand(message);
    const respondAction = (response) => respond({
      type: "AGENT_ACTION_RESPONSE",
      version: VERSION,
      run_id: message.run_id,
      step_id: message.step_id,
      command_id: message.command_id,
      ...(message.lease_id !== undefined ? { lease_id: message.lease_id } : {}),
      agent_role: message.agent_role,
      action: message.action,
      ...response,
      content_build: EXTENSION_BUILD,
      protocol: EXTENSION_PROTOCOL,
      page_kind: pageKind(),
    });
    if (error) {
      respondAction({ status: "failed", data: {}, error });
      return;
    }
    const task = runCommandOnce(message, () => {
      if (message.action === "extract_profile" && !challengeDetected() && pageKind() !== "unsupported") {
        return preparedProfileResponse();
      }
      return execute(message);
    });
    task.then(respondAction);
    return true;
  });

  let readySent = false;
  let readyRetryTimer = null;
  let readyRetryAttempt = 0;
  let readyGeneration = 0;

  function clearReadyRetry() {
    if (readyRetryTimer === null) return;
    clearTimeout(readyRetryTimer);
    readyRetryTimer = null;
  }

  function scheduleReadyRetry() {
    if (readyRetryTimer !== null) return;
    const index = Math.min(readyRetryAttempt, READY_RETRY_DELAYS_MS.length - 1);
    const delay = READY_RETRY_DELAYS_MS[index];
    readyRetryAttempt = Math.min(readyRetryAttempt + 1, READY_RETRY_DELAYS_MS.length - 1);
    readyRetryTimer = setTimeout(() => {
      readyRetryTimer = null;
      if (!readySent) signalReady();
    }, delay);
  }

  function resetReadyState() {
    readyGeneration += 1;
    readySent = false;
    readyRetryAttempt = 0;
    clearReadyRetry();
  }

  function signalReady() {
    if (readySent || !["people_search", "public_profile", "public_profile_detail", "public_contact_info", "recruiter_search", "recruiter_profile"].includes(pageKind())) return;
    readySent = true;
    clearReadyRetry();
    const generation = ++readyGeneration;
    const kind = pageKind();
    try {
      Promise.resolve().then(() => chrome.runtime.sendMessage({
        type: "AGENT_PAGE_READY",
        version: VERSION,
        build: EXTENSION_BUILD,
        protocol: EXTENSION_PROTOCOL,
      }))
        .then((response) => {
          if (generation !== readyGeneration) return;
          // Keep the one-line failure latch for older content/worker contract tests.
          if (!response?.ok) readySent = false;
          const retryRequested = !response?.ok || response?.retry === true || response?.accepted === false;
          if (retryRequested) {
            readySent = false;
            scheduleReadyRetry();
            return;
          }
          // `accepted` is optional for the old worker contract. A new worker
          // uses it only to tell an inactive Recruiter tab to retry.
          if (kind.startsWith("recruiter_") && response?.accepted !== undefined && response.accepted !== true) {
            readySent = false;
            scheduleReadyRetry();
            return;
          }
          readyRetryAttempt = 0;
          clearReadyRetry();
        })
        .catch(() => {
          if (generation !== readyGeneration) return;
          readySent = false;
          scheduleReadyRetry();
        });
    } catch (_error) {
      if (generation !== readyGeneration) return;
      readySent = false;
      scheduleReadyRetry();
    }
  }

  addEventListener("pageshow", () => { resetReadyState(); signalReady(); });
  addEventListener("focus", signalReady);
  addEventListener("blur", () => {
    resetReadyState();
    if (pageKind().startsWith("recruiter_")) scheduleReadyRetry();
  });
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
      resetReadyState();
      if (pageKind().startsWith("recruiter_")) scheduleReadyRetry();
    } else signalReady();
  });
  startNavigationWatcher();
  signalReady();
})();
