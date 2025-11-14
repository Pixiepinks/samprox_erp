(function () {
        const pageConfig = window.responsibilityPageConfig || {};
        const token = localStorage.getItem("samprox_token");
        const userRaw = localStorage.getItem("samprox_user");
        let user;

        try {
            user = userRaw ? JSON.parse(userRaw) : null;
        } catch (_) {
            user = null;
        }

        if (!token || !user) {
            const fallback = pageConfig.loginRedirect || "/";
            if (window.location.pathname !== fallback) {
                window.location.href = fallback;
            }
            return;
        }

        if (Array.isArray(pageConfig.allowedRoles) && pageConfig.allowedRoles.length > 0) {
            if (!pageConfig.allowedRoles.includes(user?.role)) {
                const redirectTarget = pageConfig.allowedRedirect || "/";
                if (redirectTarget && window.location.pathname !== redirectTarget) {
                    window.location.replace(redirectTarget);
                    return;
                }
            }
        }

        if (Array.isArray(pageConfig.roleRedirects)) {
            for (const rule of pageConfig.roleRedirects) {
                if (!rule || typeof rule !== "object") {
                    continue;
                }
                if (rule.role && rule.path && user?.role === rule.role && window.location.pathname !== rule.path) {
                    window.location.replace(rule.path);
                    return;
                }
            }
        }

        const roleLabels = {
            admin: "Admin",
            production_manager: "Production Manager",
            maintenance_manager: "Maintenance Manager",
            finance_manager: "Finance Manager",
            technician: "Technician",
            outside_manager: "Outside Manager",
        };

        const userNameEl = document.getElementById("user-name");
        const userRoleEl = document.getElementById("user-role");
        const logoutButton = document.getElementById("logout");

        userNameEl.textContent = user?.name || "Unknown";
        userRoleEl.textContent = roleLabels[user?.role] || "";

        logoutButton.addEventListener("click", async () => {
            try {
                await fetch("/api/auth/logout", { method: "POST", credentials: "include" });
            } catch (error) {
                console.error("Failed to log out", error);
            } finally {
                localStorage.removeItem("samprox_token");
                localStorage.removeItem("samprox_user");
                window.location.href = "/";
            }
        });

        const authHeaders = {
            Accept: "application/json",
            ...(token ? { Authorization: `Bearer ${token}` } : {}),
        };

        const responsibilityTableBody = document.getElementById("responsibility-table-body");
        const responsibilityEmptyState = document.getElementById("responsibility-empty");
        const responsibilityMessage = document.getElementById("responsibility-message");
        const responsibilityWeeklyForm = document.getElementById("responsibility-weekly-form");
        const responsibilityWeeklyMessage = document.getElementById("responsibility-weekly-message");
        const responsibilityWeeklyStartInput = document.getElementById("responsibility-weekly-start");
        const responsibilityWeeklyEmailInput = document.getElementById("responsibility-weekly-email");
        const responsibilityWeeklySendButton = document.getElementById("responsibility-weekly-send");
        const responsibilityCreateButton = document.getElementById("responsibility-create-button");
        const responsibilityModal = document.getElementById("responsibility-modal");
        const responsibilityModalTitle = document.getElementById("responsibility-modal-title");
        const responsibilityModalClose = document.getElementById("responsibility-modal-close");
        const responsibilityCancelButton = document.getElementById("responsibility-cancel");
        const responsibilityForm = document.getElementById("responsibility-form");
        const responsibilityFormError = document.getElementById("responsibility-form-error");
        const responsibilityTitleInput = document.getElementById("responsibility-title");
        const responsibilityDescriptionInput = document.getElementById("responsibility-description");
        const responsibilityDateInput = document.getElementById("responsibility-date");
        const responsibilityRecurrenceSelect = document.getElementById("responsibility-recurrence");
        const responsibilityCustomDays = document.getElementById("responsibility-custom-days");
        const responsibilityCustomWeekdays = document.getElementById("responsibility-custom-weekdays");
        const responsibilityAssigneeSelect = document.getElementById("responsibility-assignee");
        const responsibilityDetailInput = document.getElementById("responsibility-detail");
        const responsibilityActionSelect = document.getElementById("responsibility-action");
        const responsibilityDelegatedField = document.getElementById("responsibility-delegated-field");
        const responsibilityDelegatedSelect = document.getElementById("responsibility-delegated-to");
        const responsibilityProgressInput = document.getElementById("responsibility-progress");
        const responsibilityRecipientInput = document.getElementById("responsibility-recipient");
        const responsibilityActionNotesInput = document.getElementById("responsibility-action-notes");
        const responsibilitySubmitButton = document.getElementById("responsibility-submit");
        const responsibilityPerfSection = document.getElementById("responsibility-performance-section");
        const responsibilityPerfUomHidden = document.getElementById("responsibility-perf-uom");
        const responsibilityPerfUomSearch = document.getElementById("responsibility-perf-uom-input");
        const responsibilityPerfUomHint = document.getElementById("responsibility-perf-uom-hint");
        const responsibilityPerfUomError = document.getElementById("responsibility-perf-uom-error");
        const responsibilityPerfResponsibleInput = document.getElementById("responsibility-perf-responsible");
        const responsibilityPerfResponsiblePrefix = document.getElementById("responsibility-perf-responsible-prefix");
        const responsibilityPerfResponsibleSuffix = document.getElementById("responsibility-perf-responsible-suffix");
        const responsibilityPerfResponsibleHint = document.getElementById("responsibility-perf-responsible-hint");
        const responsibilityPerfResponsibleError = document.getElementById("responsibility-perf-responsible-error");
        const responsibilityPerfActualInput = document.getElementById("responsibility-perf-actual");
        const responsibilityPerfActualPrefix = document.getElementById("responsibility-perf-actual-prefix");
        const responsibilityPerfActualSuffix = document.getElementById("responsibility-perf-actual-suffix");
        const responsibilityPerfActualHint = document.getElementById("responsibility-perf-actual-hint");
        const responsibilityPerfActualError = document.getElementById("responsibility-perf-actual-error");
        const responsibilityPerfMetricInput = document.getElementById("responsibility-perf-metric");
        const responsibilityPerfMetricBadge = document.getElementById("responsibility-perf-metric-badge");
        const responsibilityPerformanceAlert = document.getElementById("responsibility-performance-alert");
        const responsibilitySortHeaders = document.querySelectorAll("[data-sort-key]");
        const responsibilityModalOverlay = responsibilityModal?.querySelector("[data-modal-dismiss]");
        const responsibilityActionNotesModal = document.getElementById("responsibility-action-notes-modal");
        const responsibilityActionNotesClose = document.getElementById("responsibility-action-notes-close");
        const responsibilityActionNotesTextarea = document.getElementById("responsibility-action-notes-text");
        const responsibilityActionNotesError = document.getElementById("responsibility-action-notes-error");
        const responsibilityActionNotesSave = document.getElementById("responsibility-action-notes-save");
        const responsibilityActionNotesCancel = document.getElementById("responsibility-action-notes-cancel");
        const responsibilityActionNotesOverlay = responsibilityActionNotesModal?.querySelector("[data-modal-dismiss]");
        const responsibilityModalDefaultTitle =
            responsibilityModalTitle?.textContent?.trim() || "New responsibility";
        const responsibilitySubmitDefaultText =
            responsibilitySubmitButton?.textContent?.trim() || "Save responsibility";
        let responsibilityEditingTaskId = null;
        let responsibilityEditingTask = null;
        let responsibilityTasks = [];
        const responsibilitySortState = { key: "metric", direction: "desc" };

        const RESPONSIBILITY_WEEKDAY_NAMES = [
            "Monday",
            "Tuesday",
            "Wednesday",
            "Thursday",
            "Friday",
            "Saturday",
            "Sunday",
        ];

        const RESPONSIBILITY_RECURRENCE_LABELS = {
            does_not_repeat: "Does not repeat",
            monday_to_friday: "Monday to Friday",
            daily: "Daily",
            weekly: "Weekly on (Today)",
            monthly: "Monthly on (Today)",
            annually: "Annually on (Today)",
            custom: "Custom",
        };

        const RESPONSIBILITY_STATUS_LABELS = {
            planned: "Planned",
            in_progress: "In progress",
            completed: "Completed",
            cancelled: "Cancelled",
        };

        const RESPONSIBILITY_ACTION_LABELS = {
            done: "Done",
            delegated: "Delegated",
            deferred: "Deferred",
            discussed: "Discussed",
            deleted: "Deleted",
        };

        const ACTIONS_REQUIRING_NOTES = new Set(["delegated", "deferred", "discussed", "deleted"]);

        const PERFORMANCE_UNITS = Array.isArray(pageConfig.perfUnitOptions)
            ? pageConfig.perfUnitOptions
            : [];
        const PERFORMANCE_UNIT_MAP = new Map(
            Array.isArray(PERFORMANCE_UNITS)
                ? PERFORMANCE_UNITS.map((unit) => [unit.key, unit])
                : []
        );
        const PERFORMANCE_UNIT_LABEL_MAP = new Map(
            Array.isArray(PERFORMANCE_UNITS)
                ? PERFORMANCE_UNITS.map((unit) => [unit.label.toLowerCase(), unit.key])
                : []
        );
        const METRIC_GREEN_THRESHOLD = 100;
        const METRIC_AMBER_THRESHOLD = 80;
        const PERFORMANCE_DEFAULTS = {
            uom: "percentage_pct",
            responsible: 100,
            actual: 0,
        };

        const PROGRESS_COLOR_RED = [217, 45, 32];
        const PROGRESS_COLOR_YELLOW = [254, 200, 75];
        const PROGRESS_COLOR_GREEN = [18, 183, 106];
        const PROGRESS_COLOR_GRAY = [152, 162, 179];

        const clampProgressValue = (value) => {
            if (value === null || value === undefined || value === "") {
                return 0;
            }
            const parsed = Number.parseFloat(value);
            if (Number.isNaN(parsed)) {
                return 0;
            }
            const rounded = Math.round(parsed);
            return Math.min(100, Math.max(0, rounded));
        };

        const interpolateProgressColor = (start, end, ratio) => {
            return start.map((component, index) => component + (end[index] - component) * ratio);
        };

        const toRgbString = (components) => {
            const [r, g, b] = components.map((component) => Math.round(component));
            return `rgb(${r}, ${g}, ${b})`;
        };

        const resolveProgressColor = (value, action) => {
            if (action === "deleted") {
                return toRgbString(PROGRESS_COLOR_GRAY);
            }
            if (value <= 0) {
                return toRgbString(PROGRESS_COLOR_RED);
            }
            if (value >= 100) {
                return toRgbString(PROGRESS_COLOR_GREEN);
            }
            if (value <= 50) {
                const ratio = value / 50;
                const color = interpolateProgressColor(PROGRESS_COLOR_RED, PROGRESS_COLOR_YELLOW, ratio);
                return toRgbString(color);
            }
            const ratio = (value - 50) / 50;
            const color = interpolateProgressColor(PROGRESS_COLOR_YELLOW, PROGRESS_COLOR_GREEN, ratio);
            return toRgbString(color);
        };

        const getPerformanceUnit = (key) => {
            if (!key) {
                return null;
            }
            const normalized = String(key).trim().toLowerCase();
            return PERFORMANCE_UNIT_MAP.get(normalized) || null;
        };

        const findPerformanceUnitKey = (value) => {
            if (!value) {
                return null;
            }
            const normalized = String(value).trim().toLowerCase();
            if (!normalized) {
                return null;
            }
            if (PERFORMANCE_UNIT_MAP.has(normalized)) {
                return normalized;
            }
            return PERFORMANCE_UNIT_LABEL_MAP.get(normalized) || null;
        };

        const formatPerformanceUnitLabel = (unitKey) => {
            const unit = getPerformanceUnit(unitKey);
            if (!unit) {
                return unitKey || "—";
            }
            return unit.label || unit.key;
        };

        const minutesFromDate = (value) => {
            if (!value) {
                return null;
            }
            const parts = String(value).split("-").map((part) => Number.parseInt(part, 10));
            if (parts.length !== 3 || parts.some((part) => Number.isNaN(part))) {
                return null;
            }
            const [year, month, day] = parts;
            const utcDate = Date.UTC(year, month - 1, day);
            if (Number.isNaN(utcDate)) {
                return null;
            }
            return Math.floor(utcDate / 60000);
        };

        const minutesFromTime = (value) => {
            if (!value) {
                return null;
            }
            const [hours, minutes] = String(value)
                .split(":")
                .map((part) => Number.parseInt(part, 10));
            if (Number.isNaN(hours) || Number.isNaN(minutes)) {
                return null;
            }
            if (hours < 0 || hours > 23 || minutes < 0 || minutes > 59) {
                return null;
            }
            return hours * 60 + minutes;
        };

        const parseNumber = (value) => {
            if (value === null || value === undefined) {
                return null;
            }
            if (typeof value === "number") {
                return Number.isFinite(value) ? value : null;
            }
            const text = String(value).replace(/,/g, "").trim();
            if (!text) {
                return null;
            }
            const parsed = Number.parseFloat(text);
            return Number.isFinite(parsed) ? parsed : null;
        };

        const normalizePerformanceInput = (unitKey, rawValue) => {
            const unit = getPerformanceUnit(unitKey);
            if (!unit) {
                return null;
            }
            if (rawValue === null || rawValue === undefined || rawValue === "") {
                return null;
            }

            if (unit.inputType === "date") {
                return minutesFromDate(rawValue);
            }

            if (unit.inputType === "time") {
                return minutesFromTime(rawValue);
            }

            const numeric = parseNumber(rawValue);
            if (numeric === null) {
                return null;
            }

            if (!unit.allowsNegative && numeric < 0) {
                return null;
            }
            if (unit.minValue !== null && unit.minValue !== undefined && numeric < unit.minValue) {
                return null;
            }
            if (unit.maxValue !== null && unit.maxValue !== undefined && numeric > unit.maxValue) {
                return null;
            }

            if (unit.integerOnly) {
                return Math.round(numeric);
            }

            if (unit.baseMinutesFactor) {
                return numeric * unit.baseMinutesFactor;
            }

            return numeric;
        };

        const clampMetricValue = (value, allowsNegative) => {
            if (value === null || value === undefined || Number.isNaN(value)) {
                return null;
            }
            const lowerBound = allowsNegative ? -200 : 0;
            const upperBound = 200;
            return Math.min(upperBound, Math.max(lowerBound, value));
        };

        const computeMetricValue = (unitKey, responsibleValue, actualValue) => {
            const unit = getPerformanceUnit(unitKey);
            if (!unit) {
                return { value: null, display: "—" };
            }
            const normalizedResponsible = normalizePerformanceInput(unitKey, responsibleValue);
            const normalizedActual = normalizePerformanceInput(unitKey, actualValue);
            if (normalizedResponsible === null || normalizedActual === null) {
                return { value: null, display: "—" };
            }
            if (normalizedResponsible === 0) {
                return { value: 0, display: "0.0%" };
            }
            const ratio = (normalizedActual / normalizedResponsible) * 100;
            const rounded = clampMetricValue(Number.parseFloat(ratio.toFixed(1)), unit.allowsNegative);
            const display = `${rounded.toFixed(1)}%`;
            return { value: rounded, display };
        };

        const classifyMetricValue = (value) => {
            if (value === null || value === undefined || Number.isNaN(value)) {
                return "neutral";
            }
            if (value >= METRIC_GREEN_THRESHOLD) {
                return "good";
            }
            if (value >= METRIC_AMBER_THRESHOLD) {
                return "caution";
            }
            return "risk";
        };

        const formatPerformanceValueForInput = (unitKey, value) => {
            if (value === null || value === undefined) {
                return "";
            }
            const unit = getPerformanceUnit(unitKey);
            if (!unit) {
                return String(value);
            }
            if (unit.inputType === "date" || unit.inputType === "time") {
                return String(value);
            }
            const numeric = parseNumber(value);
            if (numeric === null) {
                return "";
            }
            if (unit.integerOnly) {
                return String(Math.round(numeric));
            }
            if (Number.isInteger(unit.decimals) && unit.decimals >= 0) {
                return numeric.toFixed(unit.decimals);
            }
            return String(numeric);
        };

        const formatPerformanceSubmissionValue = (unitKey, rawValue) => {
            const unit = getPerformanceUnit(unitKey);
            if (!unit) {
                return rawValue;
            }
            if (unit.inputType === "date" || unit.inputType === "time") {
                return rawValue;
            }
            const numeric = parseNumber(rawValue);
            if (numeric === null) {
                return rawValue;
            }
            if (unit.integerOnly) {
                return String(Math.round(numeric));
            }
            if (Number.isInteger(unit.decimals) && unit.decimals >= 0) {
                return numeric.toFixed(unit.decimals);
            }
            return String(numeric);
        };

        const clearPerformanceErrors = () => {
            [
                responsibilityPerfUomError,
                responsibilityPerfResponsibleError,
                responsibilityPerfActualError,
            ].forEach((element) => {
                if (element) {
                    element.textContent = "";
                    element.setAttribute("hidden", "");
                }
            });
            if (responsibilityPerfSection) {
                responsibilityPerfSection.classList.remove("has-error");
            }
        };

        const setPerformanceError = (element, message) => {
            if (!element) {
                return;
            }
            element.textContent = message;
            element.removeAttribute("hidden");
            responsibilityPerfSection?.classList.add("has-error");
        };

        const updatePerformanceMetricDisplay = (metric) => {
            const value = metric?.value;
            const display = metric?.display ?? "—";
            if (responsibilityPerfMetricInput) {
                responsibilityPerfMetricInput.value = display;
            }
            if (responsibilityPerfMetricBadge) {
                const state = classifyMetricValue(value);
                responsibilityPerfMetricBadge.textContent = display;
                responsibilityPerfMetricBadge.classList.remove(
                    "responsibility-performance__metric-badge--good",
                    "responsibility-performance__metric-badge--caution",
                    "responsibility-performance__metric-badge--risk",
                    "responsibility-performance__metric-badge--neutral"
                );
                responsibilityPerfMetricBadge.classList.add(
                    `responsibility-performance__metric-badge--${state}`
                );
            }
        };

        const updatePerformanceMetric = () => {
            const unitKey = responsibilityPerfUomHidden?.value;
            if (!unitKey) {
                updatePerformanceMetricDisplay({ value: null, display: "—" });
                return;
            }
            const metric = computeMetricValue(
                unitKey,
                responsibilityPerfResponsibleInput?.value,
                responsibilityPerfActualInput?.value
            );
            updatePerformanceMetricDisplay(metric);
        };

        const updatePerformancePlaceholders = (unitKey) => {
            const unit = getPerformanceUnit(unitKey);
            const prefixTargets = [responsibilityPerfResponsiblePrefix, responsibilityPerfActualPrefix];
            const suffixTargets = [responsibilityPerfResponsibleSuffix, responsibilityPerfActualSuffix];
            const hints = [responsibilityPerfResponsibleHint, responsibilityPerfActualHint];
            prefixTargets.forEach((element) => {
                if (element) {
                    element.textContent = unit?.prefix || "";
                    element.toggleAttribute("hidden", !unit?.prefix);
                }
            });
            suffixTargets.forEach((element) => {
                if (element) {
                    element.textContent = unit?.suffix || "";
                    element.toggleAttribute("hidden", !unit?.suffix);
                }
            });
            hints.forEach((element) => {
                if (element) {
                    element.textContent = unit?.helperHint || "";
                    element.toggleAttribute("hidden", !unit?.helperHint);
                }
            });
            if (responsibilityPerfUomHint) {
                const negativeHint = unit?.allowsNegative
                    ? "Negative values are allowed for this unit."
                    : "Only positive values are allowed.";
                responsibilityPerfUomHint.textContent = unit
                    ? `${unit.label} selected.`
                    : "";
                responsibilityPerfUomHint.toggleAttribute("hidden", !unit);
                if (unit && !unit.allowsNegative) {
                    responsibilityPerfUomHint.textContent += " Only positive values are allowed.";
                } else if (unit && unit.allowsNegative) {
                    responsibilityPerfUomHint.textContent += ` ${negativeHint}`;
                }
            }
        };

        const configurePerformanceInput = (input, unit) => {
            if (!input) {
                return;
            }
            input.value = input.value; // preserve value when changing type in some browsers
            if (!unit) {
                input.type = "text";
                input.value = "";
                input.placeholder = "";
                input.inputMode = "text";
                input.disabled = true;
                input.min = "";
                input.max = "";
                input.step = "";
                return;
            }

            input.disabled = false;
            input.removeAttribute("min");
            input.removeAttribute("max");
            input.removeAttribute("step");

            if (unit.inputType === "date") {
                input.type = "date";
                input.inputMode = "numeric";
                return;
            }

            if (unit.inputType === "time") {
                input.type = "time";
                input.step = "60";
                input.inputMode = "numeric";
                return;
            }

            input.type = "number";
            input.inputMode = unit.integerOnly ? "numeric" : "decimal";

            if (unit.minValue !== null && unit.minValue !== undefined) {
                input.min = unit.minValue;
            }
            if (unit.maxValue !== null && unit.maxValue !== undefined) {
                input.max = unit.maxValue;
            }
            if (unit.integerOnly) {
                input.step = "1";
            } else if (Number.isInteger(unit.decimals) && unit.decimals > 0) {
                input.step = Number.parseFloat(`0.${"0".repeat(unit.decimals - 1)}1`).toString();
            } else {
                input.step = "any";
            }
        };

        const applyPerformanceUnit = (unitKey, { preserveValues = false, responsibleValue, actualValue } = {}) => {
            const unit = getPerformanceUnit(unitKey);
            if (responsibilityPerfUomHidden) {
                responsibilityPerfUomHidden.value = unit ? unit.key : "";
            }
            if (responsibilityPerfUomSearch) {
                responsibilityPerfUomSearch.value = unit ? unit.label : "";
            }

            configurePerformanceInput(responsibilityPerfResponsibleInput, unit);
            configurePerformanceInput(responsibilityPerfActualInput, unit);
            updatePerformancePlaceholders(unit?.key);

            if (!preserveValues) {
                if (responsibilityPerfResponsibleInput) {
                    responsibilityPerfResponsibleInput.value = unit && responsibleValue !== undefined
                        ? formatPerformanceValueForInput(unit.key, responsibleValue)
                        : "";
                }
                if (responsibilityPerfActualInput) {
                    responsibilityPerfActualInput.value = unit && actualValue !== undefined
                        ? formatPerformanceValueForInput(unit.key, actualValue)
                        : "";
                }
            }

            updatePerformanceMetric();
        };

        const resetPerformanceFields = () => {
            clearPerformanceErrors();
            if (responsibilityPerformanceAlert) {
                responsibilityPerformanceAlert.setAttribute("hidden", "");
            }
            applyPerformanceUnit("", { preserveValues: false });
            updatePerformanceMetricDisplay({ value: null, display: "—" });
        };

        const populatePerformanceFields = (task) => {
            const unitKey = task?.perfUom || "";
            const responsibleValue = task?.perfResponsibleValue ?? task?.perf_responsible_value;
            const actualValue = task?.perfActualValue ?? task?.perf_actual_value;
            applyPerformanceUnit(unitKey, {
                preserveValues: false,
                responsibleValue,
                actualValue,
            });
            updatePerformanceMetricDisplay({
                value: task?.perfMetricValue ?? null,
                display: task?.perfMetricDisplay ?? "—",
            });
            if (
                responsibilityPerformanceAlert &&
                unitKey === PERFORMANCE_DEFAULTS.uom &&
                Number.parseFloat(responsibleValue ?? 0) === PERFORMANCE_DEFAULTS.responsible &&
                Number.parseFloat(actualValue ?? 0) === PERFORMANCE_DEFAULTS.actual
            ) {
                responsibilityPerformanceAlert.removeAttribute("hidden");
            } else if (responsibilityPerformanceAlert) {
                responsibilityPerformanceAlert.setAttribute("hidden", "");
            }
        };

        const validatePerformanceInputs = () => {
            clearPerformanceErrors();
            const unitKey = responsibilityPerfUomHidden?.value;
            const unit = getPerformanceUnit(unitKey);
            if (!unit) {
                setPerformanceError(responsibilityPerfUomError, "Select a unit of measure.");
                responsibilityPerfUomSearch?.focus();
                return false;
            }

            const responsibleValue = responsibilityPerfResponsibleInput?.value;
            const actualValue = responsibilityPerfActualInput?.value;
            if (!responsibleValue) {
                setPerformanceError(responsibilityPerfResponsibleError, "Enter the responsible target.");
                responsibilityPerfResponsibleInput?.focus();
                return false;
            }
            if (!actualValue) {
                setPerformanceError(responsibilityPerfActualError, "Enter the actual value.");
                responsibilityPerfActualInput?.focus();
                return false;
            }

            const normalizedResponsible = normalizePerformanceInput(unitKey, responsibleValue);
            if (normalizedResponsible === null) {
                setPerformanceError(
                    responsibilityPerfResponsibleError,
                    "Enter a valid responsible target."
                );
                responsibilityPerfResponsibleInput?.focus();
                return false;
            }

            const normalizedActual = normalizePerformanceInput(unitKey, actualValue);
            if (normalizedActual === null) {
                setPerformanceError(
                    responsibilityPerfActualError,
                    "Enter a valid actual value."
                );
                responsibilityPerfActualInput?.focus();
                return false;
            }

            return true;
        };

        const resolveProgressTextColor = (value, action) => {
            if (action === "deleted") {
                return "#1D2939";
            }
            return value >= 45 ? "#FFFFFF" : "#1D2939";
        };

        const renderResponsibilityProgress = (task) => {
            const action = task?.action || "";
            const progressValue = clampProgressValue(task?.progress);
            const statusText = formatResponsibilityStatus(task?.status);
            const displayText = `${progressValue}%`;
            const color = resolveProgressColor(progressValue, action);
            const textColor = resolveProgressTextColor(progressValue, action);
            const ariaText = statusText ? `${displayText} complete – ${statusText}` : `${displayText} complete`;
            const tooltipText = statusText ? `${displayText} complete • ${statusText}` : `${displayText} complete`;

            const container = document.createElement("div");
            container.className = "responsibility-progress";

            const track = document.createElement("div");
            track.className = "responsibility-progress__track";
            track.setAttribute("role", "progressbar");
            track.setAttribute("aria-valuemin", "0");
            track.setAttribute("aria-valuemax", "100");
            track.setAttribute("aria-valuenow", String(progressValue));
            track.setAttribute("aria-valuetext", ariaText);
            track.title = tooltipText;

            const fill = document.createElement("div");
            fill.className = "responsibility-progress__fill";
            fill.style.width = `${progressValue}%`;
            fill.style.backgroundColor = color;
            if (progressValue > 0) {
                fill.style.minWidth = "4px";
            }

            const text = document.createElement("span");
            text.className = "responsibility-progress__text";
            text.textContent = displayText;
            text.style.color = textColor;

            track.append(fill, text);
            container.append(track);

            return container;
        };

        const updateDelegatedVisibility = (actionValue) => {
            if (!responsibilityDelegatedField || !responsibilityDelegatedSelect) {
                return;
            }
            const requiresDelegated = actionValue === "delegated";
            responsibilityDelegatedField.hidden = !requiresDelegated;
            if (requiresDelegated) {
                responsibilityDelegatedSelect.setAttribute("required", "required");
            } else {
                responsibilityDelegatedSelect.removeAttribute("required");
                responsibilityDelegatedSelect.value = "";
            }
        };

        const setPlanMessage = (element, message, variant = "info") => {
            if (!element) {
                return;
            }

            const classes = ["responsibility-plan__message", `responsibility-plan__message--${variant}`];
            element.className = classes.join(" ");
            if (!message) {
                element.hidden = true;
                element.textContent = "";
                return;
            }

            element.hidden = false;
            element.textContent = message;
        };

        const clearResponsibilityFormError = () => {
            if (!responsibilityFormError) {
                return;
            }
            responsibilityFormError.hidden = true;
            responsibilityFormError.textContent = "";
        };

        const showResponsibilityFormError = (message) => {
            if (!responsibilityFormError) {
                return;
            }
            responsibilityFormError.hidden = false;
            responsibilityFormError.textContent = message;
        };

        const formatResponsibilityDate = (iso) => {
            if (!iso) {
                return "—";
            }
            try {
                const date = new Date(iso);
                return date.toLocaleDateString(undefined, {
                    weekday: "short",
                    month: "short",
                    day: "numeric",
                    year: "numeric",
                });
            } catch (_) {
                return "—";
            }
        };

        const formatResponsibilityDateTime = (value) => {
            if (!value) {
                return "—";
            }
            try {
                const date = new Date(value);
                return date.toLocaleString(undefined, {
                    year: "numeric",
                    month: "short",
                    day: "numeric",
                    hour: "2-digit",
                    minute: "2-digit",
                });
            } catch (_) {
                return "—";
            }
        };

        const formatResponsibilityStatus = (status) => {
            if (!status) {
                return "";
            }
            return RESPONSIBILITY_STATUS_LABELS[status] || status;
        };

        const formatResponsibilityAction = (action) => {
            if (!action) {
                return "—";
            }
            return RESPONSIBILITY_ACTION_LABELS[action] || action;
        };

        const resetResponsibilityFormState = () => {
            responsibilityEditingTaskId = null;
            responsibilityEditingTask = null;
            clearResponsibilityFormError();
            if (responsibilityForm) {
                responsibilityForm.reset();
            }
            if (responsibilityActionNotesInput) {
                responsibilityActionNotesInput.value = "";
            }
            if (responsibilityProgressInput) {
                responsibilityProgressInput.value = "0";
            }
            responsibilityActionPreviousValue = "";
            responsibilityActionModalPreviousValue = "";
            updateDelegatedVisibility("");
            responsibilityActionNotesTextarea?.setAttribute("aria-invalid", "false");
            resetPerformanceFields();
        };

        const openResponsibilityModal = (task = null) => {
            if (!responsibilityModal) {
                return;
            }

            resetResponsibilityFormState();

            const isEditing = Boolean(task);
            responsibilityEditingTaskId = isEditing ? task.id : null;
            responsibilityEditingTask = task;

            if (responsibilityModalTitle) {
                responsibilityModalTitle.textContent = isEditing ? "Update responsibility" : responsibilityModalDefaultTitle;
            }

            if (responsibilitySubmitButton) {
                responsibilitySubmitButton.textContent = isEditing ? "Update responsibility" : responsibilitySubmitDefaultText;
            }

            if (responsibilityForm && task) {
                responsibilityTitleInput.value = task.title || "";
                responsibilityDescriptionInput.value = task.description || "";
                responsibilityDetailInput.value = task.detail || "";
                responsibilityDateInput.value = task.scheduledFor || "";
                responsibilityRecurrenceSelect.value = task.recurrence || "does_not_repeat";
                responsibilityRecipientInput.value = task.recipientEmail || "";
                responsibilityProgressInput.value = String(task.progress ?? 0);
                const taskAssigneeId = task.assigneeId ?? task.assignee?.id ?? "";
                responsibilityAssigneeSelect.value = taskAssigneeId ? String(taskAssigneeId) : "";
                responsibilityActionSelect.value = task.action || "";
                responsibilityActionPreviousValue = task.action || "";
                responsibilityActionNotesInput.value = task.actionNotes || "";
                const taskDelegatedId = task.delegatedToId ?? task.delegatedTo?.id ?? "";
                responsibilityDelegatedSelect.value = taskDelegatedId ? String(taskDelegatedId) : "";

                updateDelegatedVisibility(task.action || "");

                populatePerformanceFields(task);

                if (task.recurrence === "custom") {
                    responsibilityCustomDays.removeAttribute("hidden");
                    responsibilityCustomWeekdays.querySelectorAll("input[type='checkbox']").forEach((checkbox) => {
                        checkbox.checked = Array.isArray(task.customWeekdays)
                            ? task.customWeekdays.includes(Number.parseInt(checkbox.value, 10))
                            : false;
                    });
                } else {
                    responsibilityCustomDays.setAttribute("hidden", "");
                }
            } else if (responsibilityForm) {
                responsibilityRecurrenceSelect.value = "does_not_repeat";
                updateRecurrenceOptionLabels();
                toggleCustomWeekdayFieldset();
                responsibilityCustomWeekdays.querySelectorAll("input[type='checkbox']").forEach((checkbox) => {
                    checkbox.checked = false;
                });
                resetPerformanceFields();
            }

            responsibilityModal.removeAttribute("hidden");
            responsibilityModal.setAttribute("aria-hidden", "false");
            responsibilityModal.classList.add("modal--open");
            responsibilityTitleInput?.focus();
        };

        const closeResponsibilityModal = () => {
            if (!responsibilityModal) {
                return;
            }
            responsibilityModal.classList.remove("modal--open");
            responsibilityModal.setAttribute("hidden", "");
            responsibilityModal.setAttribute("aria-hidden", "true");
            resetResponsibilityFormState();
        };

        const appendCell = (text, options = {}) => {
            const cell = document.createElement("td");
            if (typeof text === "string" || typeof text === "number") {
                cell.textContent = text;
            } else if (text instanceof Node) {
                cell.appendChild(text);
            } else {
                cell.textContent = "—";
            }
            if (options.title) {
                cell.title = options.title;
            }
            return cell;
        };

        const createMetricPill = (task) => {
            const pill = document.createElement("span");
            pill.className = "responsibility-table__metric-pill responsibility-table__metric-pill--neutral";
            const display = task?.perfMetricDisplay || "—";
            pill.textContent = display;
            pill.title = "Achievement = Actual ÷ Responsible × 100%.";
            const numericValue = Number.parseFloat(task?.perfMetricValue);
            const state = classifyMetricValue(numericValue);
            pill.classList.remove(
                "responsibility-table__metric-pill--good",
                "responsibility-table__metric-pill--caution",
                "responsibility-table__metric-pill--risk",
                "responsibility-table__metric-pill--neutral"
            );
            pill.classList.add(`responsibility-table__metric-pill--${state}`);
            return pill;
        };

        const renderResponsibilityRow = (task) => {
            const row = document.createElement("tr");
            row.dataset.id = task?.id;

            const openButton = document.createElement("button");
            openButton.type = "button";
            openButton.className = "button button--ghost button--small";
            openButton.textContent = "Update";
            openButton.addEventListener("click", () => {
                openResponsibilityModal(task);
            });

            const progressIndicator = renderResponsibilityProgress(task);

            const createdCell = document.createElement("td");
            createdCell.textContent = formatResponsibilityDateTime(task?.createdAt) ?? "—";

            const updateCell = document.createElement("td");
            updateCell.appendChild(openButton);

            const assignerName =
                task?.assigner?.name ?? task?.assigner?.email ?? task?.assignerName ?? "—";
            const assigneeName =
                task?.assignee?.name ?? task?.assignee?.email ?? task?.assigneeName ?? "—";
            const delegatedName =
                task?.delegatedTo?.name ?? task?.delegatedTo?.email ?? task?.delegatedToName ?? "—";
            const unitLabel = formatPerformanceUnitLabel(task?.perfUom);
            const responsibleDisplay =
                task?.perfResponsibleDisplay ?? formatPerformanceValueForInput(task?.perfUom, task?.perfResponsibleValue);
            const actualDisplay =
                task?.perfActualDisplay ?? formatPerformanceValueForInput(task?.perfUom, task?.perfActualValue);
            const metricPill = createMetricPill(task);

            row.append(
                appendCell(task?.number || "—"),
                appendCell(task?.title || "—"),
                appendCell(progressIndicator),
                appendCell(task?.detail || "—"),
                appendCell(assignerName || "—"),
                appendCell(assigneeName || "—"),
                appendCell(delegatedName || "—"),
                appendCell(formatResponsibilityDate(task?.scheduledFor)),
                appendCell(formatResponsibilityAction(task?.action), { title: task?.actionNotes }),
                appendCell(unitLabel || "—"),
                appendCell(responsibleDisplay || "—"),
                appendCell(actualDisplay || "—"),
                appendCell(metricPill),
                createdCell,
                updateCell,
            );

            return row;
        };

        const getResponsibilitySortValue = (task, key) => {
            if (!task) {
                return null;
            }
            if (key === "metric") {
                const value = Number.parseFloat(task?.perfMetricValue);
                return Number.isFinite(value) ? value : null;
            }
            if (key === "actual") {
                const rawValue =
                    task?.perfActualValue ?? task?.perfActualDisplay ?? task?.perf_actual_value ?? null;
                const normalized = normalizePerformanceInput(task?.perfUom, rawValue);
                return normalized ?? null;
            }
            return null;
        };

        const sortResponsibilityTasks = (tasks) => {
            const { key, direction } = responsibilitySortState;
            if (!key) {
                return Array.isArray(tasks) ? [...tasks] : [];
            }
            const multiplier = direction === "asc" ? 1 : -1;
            return [...(Array.isArray(tasks) ? tasks : [])].sort((a, b) => {
                const aValue = getResponsibilitySortValue(a, key);
                const bValue = getResponsibilitySortValue(b, key);
                if (aValue === null && bValue === null) {
                    return 0;
                }
                if (aValue === null) {
                    return 1;
                }
                if (bValue === null) {
                    return -1;
                }
                if (aValue === bValue) {
                    return 0;
                }
                return aValue > bValue ? multiplier : -multiplier;
            });
        };

        const renderResponsibilityTable = (tasks) => {
            if (!responsibilityTableBody) {
                return;
            }

            responsibilityTableBody.innerHTML = "";
            if (!Array.isArray(tasks) || tasks.length === 0) {
                responsibilityEmptyState?.removeAttribute("hidden");
                return;
            }

            responsibilityEmptyState?.setAttribute("hidden", "");

            tasks.forEach((task) => {
                const row = renderResponsibilityRow(task);
                responsibilityTableBody.appendChild(row);
            });
        };

        const updateSortIndicators = () => {
            responsibilitySortHeaders.forEach((header) => {
                const key = header.getAttribute("data-sort-key");
                if (!key) {
                    header.removeAttribute("data-sort-direction");
                    return;
                }
                if (responsibilitySortState.key === key) {
                    header.setAttribute("data-sort-direction", responsibilitySortState.direction);
                } else {
                    header.removeAttribute("data-sort-direction");
                }
            });
        };

        const refreshResponsibilityTable = () => {
            const sorted = sortResponsibilityTasks(responsibilityTasks);
            renderResponsibilityTable(sorted);
            updateSortIndicators();
        };

        const updateResponsibilityTable = (tasks) => {
            responsibilityTasks = Array.isArray(tasks) ? tasks : [];
            refreshResponsibilityTable();
        };

        const fetchResponsibilities = async () => {
            if (!token) {
                return;
            }

            try {
                const response = await fetch("/api/responsibilities", {
                    headers: authHeaders,
                });

                if (!response.ok) {
                    const errorData = await response.json().catch(() => null);
                    throw new Error(errorData?.msg || "Unable to load responsibilities.");
                }

                const data = await response.json();
                updateResponsibilityTable(Array.isArray(data) ? data : []);
            } catch (error) {
                console.error("Failed to load responsibilities", error);
                setPlanMessage(
                    responsibilityMessage,
                    error?.message || "Unable to load responsibility plan.",
                    "error",
                );
            }
        };

        const populateResponsibilityAssignees = (assignees = []) => {
            if (!responsibilityAssigneeSelect || !responsibilityDelegatedSelect) {
                return;
            }

            const renderOption = (option) => {
                const element = document.createElement("option");
                element.value = option?.id;
                element.textContent = option?.name;
                return element;
            };

            const fragment = document.createDocumentFragment();
            assignees.forEach((assignee) => {
                fragment.appendChild(renderOption({ id: assignee.id, name: assignee.name }));
            });

            const delegatedFragment = document.createDocumentFragment();
            assignees.forEach((assignee) => {
                delegatedFragment.appendChild(renderOption({ id: assignee.id, name: assignee.name }));
            });

            responsibilityAssigneeSelect.append(fragment.cloneNode(true));
            responsibilityDelegatedSelect.append(delegatedFragment.cloneNode(true));
        };

        const fetchResponsibilityAssignees = async () => {
            if (!token) {
                return;
            }

            try {
                const response = await fetch("/api/responsibilities/assignees", {
                    headers: authHeaders,
                });

                if (!response.ok) {
                    const data = await response.json().catch(() => null);
                    throw new Error(data?.msg || "Unable to load managers.");
                }

                const data = await response.json();
                populateResponsibilityAssignees(Array.isArray(data) ? data : []);
            } catch (error) {
                console.error("Failed to load responsibility assignees", error);
            }
        };

        const toIsoDateString = (date) => {
            if (!(date instanceof Date) || Number.isNaN(date.getTime())) {
                return "";
            }
            const offset = date.getTimezoneOffset();
            const normalized = new Date(date.getTime() - offset * 60000);
            return normalized.toISOString().split("T")[0];
        };

        const updateRecurrenceOptionLabels = () => {
            if (!responsibilityRecurrenceSelect) {
                return;
            }

            const today = new Date();
            const weekday = today.toLocaleDateString(undefined, { weekday: "long" });
            const dayOfMonth = today.getDate();
            const monthName = today.toLocaleDateString(undefined, { month: "long" });

            Array.from(responsibilityRecurrenceSelect.options).forEach((option) => {
                const baseLabel = RESPONSIBILITY_RECURRENCE_LABELS[option.value] || option.textContent;
                if (!baseLabel) {
                    return;
                }
                if (option.value === "weekly") {
                    option.textContent = baseLabel.replace("(Today)", weekday);
                } else if (option.value === "monthly") {
                    option.textContent = baseLabel.replace("(Today)", `${dayOfMonth}${getOrdinalSuffix(dayOfMonth)}`);
                } else if (option.value === "annually") {
                    option.textContent = baseLabel.replace("(Today)", `${monthName} ${dayOfMonth}`);
                } else {
                    option.textContent = baseLabel;
                }
            });
        };

        const getOrdinalSuffix = (day) => {
            if (![11, 12, 13].includes(day % 100)) {
                switch (day % 10) {
                    case 1:
                        return "st";
                    case 2:
                        return "nd";
                    case 3:
                        return "rd";
                }
            }
            return "th";
        };

        const toggleCustomWeekdayFieldset = () => {
            if (!responsibilityCustomDays || !responsibilityRecurrenceSelect) {
                return;
            }
            const isCustom = responsibilityRecurrenceSelect.value === "custom";
            if (isCustom) {
                responsibilityCustomDays.removeAttribute("hidden");
                responsibilityCustomWeekdays.querySelectorAll("input[type='checkbox']").forEach((checkbox) => {
                    checkbox.required = true;
                });
            } else {
                responsibilityCustomDays.setAttribute("hidden", "");
                responsibilityCustomWeekdays.querySelectorAll("input[type='checkbox']").forEach((checkbox) => {
                    checkbox.checked = false;
                    checkbox.required = false;
                });
            }
        };

        const responsibilityActionPreviousNotes = new Map();
        let responsibilityActionPreviousValue = "";
        let responsibilityActionModalPreviousValue = "";

        const handleResponsibilityActionChange = (event) => {
            if (!event?.target) {
                return;
            }

            const actionValue = event.target.value;
            updateDelegatedVisibility(actionValue);
            if (ACTIONS_REQUIRING_NOTES.has(actionValue)) {
                const previousNotes = responsibilityActionPreviousNotes.get(actionValue) || "";
                if (responsibilityActionNotesTextarea) {
                    responsibilityActionNotesTextarea.value = previousNotes;
                }
                openResponsibilityActionNotesModal(responsibilityActionPreviousValue);
            } else {
                responsibilityActionNotesInput.value = "";
            }
            responsibilityActionPreviousValue = actionValue;
        };

        const responsibilityActionNotesTextareaInput = (event) => {
            if (!responsibilityActionNotesTextarea) {
                return;
            }
            responsibilityActionNotesTextarea.setAttribute("aria-invalid", event.target.value.trim().length === 0);
        };

        const responsibilityActionNotesSaveHandler = () => {
            const notes = responsibilityActionNotesTextarea.value.trim();
            if (!notes) {
                setResponsibilityActionNotesError("Notes are required for this action.");
                responsibilityActionNotesTextarea.setAttribute("aria-invalid", "true");
                responsibilityActionNotesTextarea.focus();
                return;
            }
            setResponsibilityActionNotesError();
            responsibilityActionNotesTextarea.setAttribute("aria-invalid", "false");
            responsibilityActionNotesInput.value = notes;
            responsibilityActionPreviousNotes.set(responsibilityActionPreviousValue, notes);
            closeResponsibilityActionNotesModal();
        };

        const responsibilityActionNotesCancelHandler = () => {
            setResponsibilityActionNotesError();
            responsibilityActionNotesTextarea.setAttribute("aria-invalid", "false");
            closeResponsibilityActionNotesModal(true);
        };

        const responsibilityActionNotesOverlayHandler = () => {
            setResponsibilityActionNotesError();
            closeResponsibilityActionNotesModal(true);
        };

        const responsibilityActionNotesCloseHandler = () => {
            setResponsibilityActionNotesError();
            closeResponsibilityActionNotesModal(true);
        };

        const submitResponsibilityForm = async (event) => {
            event.preventDefault();
            if (!responsibilityForm) {
                return;
            }

            const formData = new FormData(responsibilityForm);
            const payload = Object.fromEntries(formData.entries());
            const isEditingResponsibility = responsibilityEditingTaskId !== null;

            if (!payload.title) {
                showResponsibilityFormError("Title is required.");
                return;
            }

            if (!payload.scheduledFor) {
                showResponsibilityFormError("Select a scheduled date.");
                return;
            }

            if (!payload.recurrence) {
                showResponsibilityFormError("Select a recurrence option.");
                return;
            }

            if (payload.recurrence === "custom") {
                const selected = responsibilityCustomWeekdays
                    ? Array.from(responsibilityCustomWeekdays.querySelectorAll("input[type='checkbox']"))
                          .filter((checkbox) => checkbox.checked)
                          .map((checkbox) => Number.parseInt(checkbox.value, 10))
                    : [];
                if (selected.length === 0) {
                    showResponsibilityFormError("Select at least one weekday for the custom schedule.");
                    return;
                }
                payload.customWeekdays = selected;
            }

            if (!payload.action) {
                showResponsibilityFormError("Select a 5D action.");
                return;
            }

            if (ACTIONS_REQUIRING_NOTES.has(payload.action)) {
                if (!payload.actionNotes) {
                    showResponsibilityFormError("Enter discussion points or reasons for the selected action.");
                    openResponsibilityActionNotesModal(payload.action);
                    setResponsibilityActionNotesError("Notes are required for this action.");
                    return;
                }
            }

            if (payload.action === "delegated" && !payload.delegatedToId) {
                showResponsibilityFormError("Select a delegated manager when the action is Delegated.");
                return;
            }

            if (!payload.recipientEmail) {
                showResponsibilityFormError("Enter a notification email address.");
                return;
            }

            if (!validatePerformanceInputs()) {
                return;
            }

            const perfUnitKey = responsibilityPerfUomHidden?.value || payload.perfUom;
            payload.perfUom = perfUnitKey;
            payload.perfResponsibleValue = formatPerformanceSubmissionValue(
                perfUnitKey,
                responsibilityPerfResponsibleInput?.value
            );
            payload.perfActualValue = formatPerformanceSubmissionValue(
                perfUnitKey,
                responsibilityPerfActualInput?.value
            );

            if (payload.progress && (payload.progress < 0 || payload.progress > 100)) {
                showResponsibilityFormError("Progress must be between 0 and 100.");
                return;
            }

            if (payload.progress) {
                payload.progress = clampProgressValue(payload.progress);
            }

            if (payload.assigneeId === "") {
                delete payload.assigneeId;
            }

            if (payload.delegatedToId === "") {
                delete payload.delegatedToId;
            }

            const requestUrl = isEditingResponsibility
                ? `/api/responsibilities/${responsibilityEditingTaskId}`
                : "/api/responsibilities";
            const requestMethod = isEditingResponsibility ? "PUT" : "POST";

            clearResponsibilityFormError();
            if (responsibilitySubmitButton) {
                responsibilitySubmitButton.disabled = true;
                responsibilitySubmitButton.textContent = isEditingResponsibility
                    ? "Updating responsibility…"
                    : "Saving responsibility…";
            }

            try {
                const response = await fetch(requestUrl, {
                    method: requestMethod,
                    headers: {
                        ...authHeaders,
                        "Content-Type": "application/json",
                    },
                    body: JSON.stringify(payload),
                });

                if (!response.ok) {
                    const data = await response.json().catch(() => ({}));
                    if (data?.errors) {
                        const messages = Object.values(data.errors)
                            .flat()
                            .filter(Boolean)
                            .join(" ");
                        throw new Error(messages || "Unable to save responsibility.");
                    }
                    throw new Error(data?.msg || "Unable to save responsibility.");
                }

                await fetchResponsibilities();
                closeResponsibilityModal();
                const successMessage = isEditingResponsibility
                    ? "Responsibility updated."
                    : "Responsibility saved and notification sent.";
                setPlanMessage(responsibilityMessage, successMessage, "success");
            } catch (error) {
                console.error("Failed to submit responsibility", error);
                showResponsibilityFormError(error?.message || "Unable to save responsibility.");
            } finally {
                if (responsibilitySubmitButton) {
                    responsibilitySubmitButton.disabled = false;
                    responsibilitySubmitButton.textContent = responsibilitySubmitDefaultText;
                }
            }
        };

        const responsibilityModalOverlayHandler = (event) => {
            if (event.target !== responsibilityModalOverlay) {
                return;
            }
            closeResponsibilityModal();
        };

        const deleteResponsibility = async (taskId) => {
            if (!taskId) {
                return;
            }

            try {
                const response = await fetch(`/api/responsibilities/${taskId}`, {
                    method: "DELETE",
                    headers: authHeaders,
                });

                if (!response.ok) {
                    const data = await response.json().catch(() => null);
                    throw new Error(data?.msg || "Unable to delete responsibility.");
                }

                await fetchResponsibilities();
                setPlanMessage(responsibilityMessage, "Responsibility deleted.", "success");
            } catch (error) {
                console.error("Failed to delete responsibility", error);
                setPlanMessage(
                    responsibilityMessage,
                    error?.message || "Unable to delete responsibility.",
                    "error",
                );
            }
        };

        const handleResponsibilityRowAction = (event) => {
            const trigger = event.target.closest("[data-responsibility-action]");
            if (!trigger) {
                return;
            }

            const row = trigger.closest("tr[data-id]");
            if (!row) {
                return;
            }

            const taskId = Number.parseInt(row.dataset.id, 10);
            if (Number.isNaN(taskId)) {
                return;
            }

            const action = trigger.dataset.responsibilityAction;
            if (action === "delete") {
                deleteResponsibility(taskId);
            }
        };

        const responsibilityTable = responsibilityTableBody?.closest("table");
        responsibilityTable?.addEventListener("click", handleResponsibilityRowAction);

        const handleSortInteraction = (sortKey) => {
            if (!sortKey) {
                return;
            }
            if (responsibilitySortState.key === sortKey) {
                responsibilitySortState.direction =
                    responsibilitySortState.direction === "desc" ? "asc" : "desc";
            } else {
                responsibilitySortState.key = sortKey;
                responsibilitySortState.direction = sortKey === "metric" ? "desc" : "desc";
            }
            refreshResponsibilityTable();
        };

        responsibilitySortHeaders.forEach((header) => {
            header.classList.add("responsibility-table__sortable");
            header.addEventListener("click", () => {
                handleSortInteraction(header.getAttribute("data-sort-key"));
            });
            header.addEventListener("keydown", (event) => {
                if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    handleSortInteraction(header.getAttribute("data-sort-key"));
                }
            });
        });

        responsibilityPerfUomSearch?.addEventListener("input", (event) => {
            const key = findPerformanceUnitKey(event.target.value);
            if (key) {
                clearPerformanceErrors();
                applyPerformanceUnit(key, { preserveValues: false });
            }
        });

        responsibilityPerfUomSearch?.addEventListener("blur", (event) => {
            const key = findPerformanceUnitKey(event.target.value);
            if (key) {
                responsibilityPerfUomHidden.value = key;
                responsibilityPerfUomSearch.value = formatPerformanceUnitLabel(key);
                clearPerformanceErrors();
            } else if (event.target.value.trim() === "") {
                responsibilityPerfUomHidden.value = "";
                resetPerformanceFields();
            } else {
                responsibilityPerfUomHidden.value = "";
                setPerformanceError(responsibilityPerfUomError, "Select a valid unit of measure.");
            }
        });

        const handlePerformanceValueInput = (errorElement) => {
            if (errorElement) {
                errorElement.textContent = "";
                errorElement.setAttribute("hidden", "");
                responsibilityPerfSection?.classList.remove("has-error");
            }
            updatePerformanceMetric();
        };

        responsibilityPerfResponsibleInput?.addEventListener("input", () => {
            handlePerformanceValueInput(responsibilityPerfResponsibleError);
        });
        responsibilityPerfResponsibleInput?.addEventListener("blur", () => {
            const unitKey = responsibilityPerfUomHidden?.value;
            if (unitKey) {
                responsibilityPerfResponsibleInput.value = formatPerformanceSubmissionValue(
                    unitKey,
                    responsibilityPerfResponsibleInput.value
                );
            }
            updatePerformanceMetric();
        });

        responsibilityPerfActualInput?.addEventListener("input", () => {
            handlePerformanceValueInput(responsibilityPerfActualError);
        });
        responsibilityPerfActualInput?.addEventListener("blur", () => {
            const unitKey = responsibilityPerfUomHidden?.value;
            if (unitKey) {
                responsibilityPerfActualInput.value = formatPerformanceSubmissionValue(
                    unitKey,
                    responsibilityPerfActualInput.value
                );
            }
            updatePerformanceMetric();
        });

        responsibilityCreateButton?.addEventListener("click", () => openResponsibilityModal());
        responsibilityModalClose?.addEventListener("click", closeResponsibilityModal);
        responsibilityCancelButton?.addEventListener("click", closeResponsibilityModal);
        responsibilityModalOverlay?.addEventListener("click", responsibilityModalOverlayHandler);
        responsibilityForm?.addEventListener("submit", submitResponsibilityForm);
        responsibilityActionSelect?.addEventListener("change", handleResponsibilityActionChange);
        responsibilityActionNotesTextarea?.addEventListener("input", responsibilityActionNotesTextareaInput);
        responsibilityActionNotesSave?.addEventListener("click", responsibilityActionNotesSaveHandler);
        responsibilityActionNotesCancel?.addEventListener("click", responsibilityActionNotesCancelHandler);
        responsibilityActionNotesOverlay?.addEventListener("click", responsibilityActionNotesOverlayHandler);
        responsibilityActionNotesClose?.addEventListener("click", responsibilityActionNotesCloseHandler);

        const sendWeeklyPlan = async (event) => {
            event.preventDefault();
            if (!responsibilityWeeklyForm) {
                return;
            }

            responsibilityWeeklySendButton.disabled = true;
            setPlanMessage(responsibilityWeeklyMessage, "Sending weekly plan…", "info");

            const formData = new FormData(responsibilityWeeklyForm);
            const payload = Object.fromEntries(formData.entries());

            try {
                const response = await fetch("/api/responsibilities/send-weekly", {
                    method: "POST",
                    headers: {
                        ...authHeaders,
                        "Content-Type": "application/json",
                    },
                    body: JSON.stringify(payload),
                });

                if (!response.ok) {
                    const data = await response.json().catch(() => null);
                    throw new Error(data?.msg || "Unable to send weekly plan.");
                }

                const data = await response.json();
                const rangeText = `${formatResponsibilityDate(data?.startDate)} – ${formatResponsibilityDate(
                    data?.endDate,
                )}`;
                setPlanMessage(
                    responsibilityWeeklyMessage,
                    `Weekly plan sent for ${rangeText}.`,
                    "success",
                );
            } catch (error) {
                console.error("Failed to send weekly plan", error);
                setPlanMessage(
                    responsibilityWeeklyMessage,
                    error?.message || "Unable to send weekly plan.",
                    "error",
                );
            } finally {
                responsibilityWeeklySendButton.disabled = false;
            }
        };

        responsibilityWeeklyForm?.addEventListener("submit", sendWeeklyPlan);

        const initializeResponsibilityWeeklyStart = () => {
            if (!responsibilityWeeklyStartInput) {
                return;
            }
            const today = new Date();
            const day = today.getDay();
            const offset = day === 0 ? -6 : 1 - day;
            const monday = new Date(today);
            monday.setDate(today.getDate() + offset);
            responsibilityWeeklyStartInput.value = toIsoDateString(monday);
        };

        initializeResponsibilityWeeklyStart();
        toggleCustomWeekdayFieldset();
        updateRecurrenceOptionLabels();
        fetchResponsibilityAssignees();
        fetchResponsibilities();
    
})();
