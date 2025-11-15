(function () {
    const DATE_FORMATTER = new Intl.DateTimeFormat(undefined, { dateStyle: "medium" });

    function toIsoDateString(dateObj) {
        if (!(dateObj instanceof Date) || Number.isNaN(dateObj.getTime())) {
            return "";
        }
        const year = dateObj.getFullYear();
        const month = String(dateObj.getMonth() + 1).padStart(2, "0");
        const day = String(dateObj.getDate()).padStart(2, "0");
        return `${year}-${month}-${day}`;
    }

    function formatDateDisplay(isoDate) {
        if (!isoDate) {
            return "";
        }
        const parsed = new Date(isoDate);
        if (Number.isNaN(parsed.getTime())) {
            return isoDate;
        }
        return DATE_FORMATTER.format(parsed);
    }

    function setMessage(element, message, variant) {
        if (!element) {
            return;
        }
        if (!message) {
            element.textContent = "";
            element.className = "responsibility-report__message";
            element.hidden = true;
            return;
        }
        element.textContent = message;
        element.className =
            "responsibility-report__message" + (variant ? ` responsibility-report__message--${variant}` : "");
        element.hidden = false;
    }

    function updateDownloadButtons(buttons, enabled) {
        if (!Array.isArray(buttons)) {
            return;
        }
        buttons.forEach((button) => {
            if (button) {
                button.disabled = !enabled;
            }
        });
    }

    function buildExportRows(data) {
        if (!data || !Array.isArray(data.members)) {
            return [];
        }
        const rows = [];
        data.members.forEach((member) => {
            const occurrences = Array.isArray(member.occurrences) ? member.occurrences : [];
            if (occurrences.length === 0) {
                rows.push({
                    "Team member": member.name || "",
                    Date: "",
                    "Responsibility No": "",
                    Title: "No responsibilities in range",
                    Status: "",
                    "5D Action": "",
                    "Progress (%)": "",
                    "Unit of Measure": "",
                    Responsible: "",
                    Actual: "",
                    "Performance Metric": "",
                    Description: "",
                    Detail: "",
                    "Discussion Detail": "",
                    "Assigned To": "",
                    "Delegated To": "",
                });
                return;
            }
            occurrences.forEach((occurrence) => {
                const progressLabel =
                    occurrence.taskProgressLabel ||
                    (occurrence.taskProgress === 0 || occurrence.taskProgress
                        ? `${occurrence.taskProgress}%`
                        : "");
                const unitLabel =
                    occurrence.taskPerformanceUnitLabel ||
                    occurrence.taskPerformanceUnit ||
                    "";
                rows.push({
                    "Team member": member.name || "",
                    Date: formatDateDisplay(occurrence.date),
                    "Responsibility No": occurrence.taskNumber || "",
                    Title: occurrence.taskTitle || "",
                    Description: occurrence.taskDescription || "",
                    Detail: occurrence.taskDetail || "",
                    "Discussion Detail": occurrence.taskDiscussion || "",
                    Status: occurrence.taskStatus || "",
                    "5D Action": occurrence.taskAction || "",
                    "Progress (%)": progressLabel,
                    "Unit of Measure": unitLabel,
                    Responsible: occurrence.taskPerformanceResponsible || "",
                    Actual: occurrence.taskPerformanceActual || "",
                    "Performance Metric": occurrence.taskPerformanceMetric || "",
                    "Assigned To": occurrence.assigneeName || "",
                    "Delegated To": occurrence.delegatedToName || "",
                });
            });
        });
        return rows;
    }

    function downloadExcel(data, filename) {
        if (!window.XLSX) {
            console.warn("SheetJS is not available for Excel export.");
            return;
        }
        const rows = buildExportRows(data);
        const worksheet = window.XLSX.utils.json_to_sheet(rows);
        const workbook = window.XLSX.utils.book_new();
        window.XLSX.utils.book_append_sheet(workbook, worksheet, "Responsibilities");
        window.XLSX.writeFile(workbook, filename);
    }

    function downloadPdf(data, filename, summaryText) {
        const jspdf = window.jspdf || {};
        const jsPDF = jspdf.jsPDF;
        if (typeof jsPDF !== "function") {
            console.warn("jsPDF is not available for PDF export.");
            return;
        }

        const doc = new jsPDF({ orientation: "landscape", unit: "pt" });
        const marginLeft = 40;
        let cursorY = 60;

        doc.setFontSize(18);
        doc.text("Responsibilities by team member", marginLeft, cursorY);
        cursorY += 24;

        doc.setFontSize(12);
        const summaryLines = doc.splitTextToSize(summaryText || "", 760);
        summaryLines.forEach((line) => {
            doc.text(line, marginLeft, cursorY);
            cursorY += 16;
        });
        cursorY += 8;

        doc.setFontSize(11);
        if (!data || !Array.isArray(data.members) || data.members.length === 0) {
            doc.text("No responsibilities recorded for the selected filters.", marginLeft, cursorY);
            doc.save(filename);
            return;
        }

        data.members.forEach((member, index) => {
            if (cursorY > 520) {
                doc.addPage();
                cursorY = 60;
            }
            const heading = `${member.name || "Team member"} — ${member.occurrenceCount || 0} occurrence${
                (member.occurrenceCount || 0) === 1 ? "" : "s"
            } (${member.uniqueTaskCount || 0} task${member.uniqueTaskCount === 1 ? "" : "s"})`;
            doc.setFont(undefined, "bold");
            doc.text(heading, marginLeft, cursorY);
            doc.setFont(undefined, "normal");
            cursorY += 18;

            const occurrences = Array.isArray(member.occurrences) ? member.occurrences : [];
            if (occurrences.length === 0) {
                doc.text("No responsibilities scheduled in this range.", marginLeft + 12, cursorY);
                cursorY += 16;
                return;
            }

            occurrences.forEach((occurrence) => {
                if (cursorY > 540) {
                    doc.addPage();
                    cursorY = 60;
                }
                const line = `${formatDateDisplay(occurrence.date)} • ${occurrence.taskNumber || ""} • ${
                    occurrence.taskTitle || ""
                } (${occurrence.taskStatus || ""}/${occurrence.taskAction || ""})`;
                const wrapped = doc.splitTextToSize(line.trim(), 760);
                wrapped.forEach((entry) => {
                    doc.text(entry, marginLeft + 12, cursorY);
                    cursorY += 14;
                });
                const ownerLineParts = [];
                if (occurrence.assigneeName) {
                    ownerLineParts.push(`Assigned to ${occurrence.assigneeName}`);
                }
                if (occurrence.delegatedToName) {
                    ownerLineParts.push(`Delegated to ${occurrence.delegatedToName}`);
                }
                if (ownerLineParts.length > 0) {
                    const ownerLine = ownerLineParts.join(" · ");
                    doc.setFontSize(10);
                    doc.text(ownerLine, marginLeft + 24, cursorY);
                    doc.setFontSize(11);
                    cursorY += 12;
                }
                const progressLabel =
                    occurrence.taskProgressLabel ||
                    (occurrence.taskProgress === 0 || occurrence.taskProgress
                        ? `${occurrence.taskProgress}%`
                        : "");
                const unitLabel =
                    occurrence.taskPerformanceUnitLabel ||
                    occurrence.taskPerformanceUnit ||
                    "";
                const metrics = [];
                if (progressLabel) {
                    metrics.push(`Progress: ${progressLabel}`);
                }
                if (unitLabel) {
                    metrics.push(`Unit of Measure: ${unitLabel}`);
                }
                if (occurrence.taskPerformanceResponsible) {
                    metrics.push(`Responsible: ${occurrence.taskPerformanceResponsible}`);
                }
                if (occurrence.taskPerformanceActual) {
                    metrics.push(`Actual: ${occurrence.taskPerformanceActual}`);
                }
                if (occurrence.taskPerformanceMetric) {
                    metrics.push(`Performance Metric: ${occurrence.taskPerformanceMetric}`);
                }
                metrics.forEach((metricLine) => {
                    const metricWrapped = doc.splitTextToSize(metricLine, 720);
                    metricWrapped.forEach((metricText) => {
                        doc.setFontSize(10);
                        doc.text(metricText, marginLeft + 24, cursorY);
                        cursorY += 12;
                    });
                    doc.setFontSize(11);
                });
                const discussionBlocks = [
                    ["Description", occurrence.taskDescription],
                    ["Detail", occurrence.taskDetail],
                    ["Discussion", occurrence.taskDiscussion || occurrence.taskActionNotes],
                ];
                discussionBlocks.forEach(([label, value]) => {
                    const content = value && value.toString().trim();
                    if (!content) {
                        return;
                    }
                    const prefixed = `${label}: ${content}`;
                    const wrappedContent = doc.splitTextToSize(prefixed, 720);
                    wrappedContent.forEach((entry) => {
                        doc.setFontSize(10);
                        doc.text(entry, marginLeft + 24, cursorY);
                        cursorY += 12;
                    });
                    doc.setFontSize(11);
                });
                cursorY += 6;
            });

            if (index !== data.members.length - 1) {
                cursorY += 6;
            }
        });

        doc.save(filename);
    }

    function downloadPng(chart, filename) {
        if (!chart) {
            console.warn("Chart instance not available for PNG export.");
            return;
        }
        const link = document.createElement("a");
        link.href = chart.toBase64Image("image/png", 1);
        link.download = filename;
        link.click();
    }

    function initResponsibilityReport(config) {
        const {
            form,
            startInput,
            endInput,
            memberSelect,
            summaryElement,
            tableBody,
            emptyState,
            messageElement,
            chartCanvas,
            downloadButtons,
            authHeaders,
        } = config || {};

        if (!form || !startInput || !endInput || !memberSelect) {
            return;
        }

        const state = {
            chart: null,
            data: null,
            members: [],
        };

        const downloads = Array.from(downloadButtons || []);
        updateDownloadButtons(downloads, false);

        function getSelectionLabel() {
            const selectedOption = memberSelect.options[memberSelect.selectedIndex];
            return selectedOption ? selectedOption.textContent || "selected team member" : "selected team member";
        }

        function renderSummary(data) {
            if (!summaryElement) {
                return;
            }
            if (!data) {
                summaryElement.textContent = "";
                return;
            }
            const total = data.totalOccurrences || 0;
            const startDisplay = formatDateDisplay(data.startDate);
            const endDisplay = formatDateDisplay(data.endDate);
            const selection = getSelectionLabel();
            summaryElement.textContent = `Showing ${total} responsibility occurrence${
                total === 1 ? "" : "s"
            } for ${selection} from ${startDisplay} to ${endDisplay}.`;
        }

        function renderTable(data) {
            if (!tableBody) {
                return;
            }
            tableBody.innerHTML = "";
            const members = Array.isArray(data?.members) ? data.members : [];
            if (!members.length) {
                if (emptyState) {
                    emptyState.hidden = false;
                }
                return;
            }
            if (emptyState) {
                emptyState.hidden = true;
            }
            members.forEach((member) => {
                const row = document.createElement("tr");
                const nameCell = document.createElement("td");
                nameCell.textContent = member.name || "Team member";
                const occurrenceCell = document.createElement("td");
                occurrenceCell.textContent = String(member.occurrenceCount || 0);
                const uniqueCell = document.createElement("td");
                uniqueCell.textContent = String(member.uniqueTaskCount || 0);
                row.appendChild(nameCell);
                row.appendChild(occurrenceCell);
                row.appendChild(uniqueCell);
                tableBody.appendChild(row);
            });
        }

        function renderChart(data) {
            if (!chartCanvas || typeof window.Chart === "undefined") {
                return;
            }
            const ctx = chartCanvas.getContext("2d");
            if (!ctx) {
                return;
            }
            const members = Array.isArray(data?.members) ? data.members : [];
            const labels = members.map((member) => member.name || "Team member");
            const counts = members.map((member) => member.occurrenceCount || 0);
            const hasValues = counts.some((value) => Number(value) > 0);

            if (!hasValues) {
                if (state.chart) {
                    state.chart.destroy();
                    state.chart = null;
                }
                return;
            }

            const dataset = {
                label: "Responsibilities",
                data: counts,
                backgroundColor: "rgba(37, 99, 235, 0.6)",
                borderColor: "rgba(37, 99, 235, 1)",
                borderWidth: 1,
                borderRadius: 8,
            };

            if (state.chart) {
                state.chart.data.labels = labels;
                state.chart.data.datasets = [dataset];
                state.chart.update();
                return;
            }

            state.chart = new window.Chart(ctx, {
                type: "bar",
                data: {
                    labels,
                    datasets: [dataset],
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                        legend: { display: false },
                        tooltip: {
                            callbacks: {
                                label(context) {
                                    const value = context.parsed.y || 0;
                                    return `${value} occurrence${value === 1 ? "" : "s"}`;
                                },
                            },
                        },
                    },
                    scales: {
                        x: {
                            ticks: {
                                maxRotation: 45,
                                minRotation: 0,
                                autoSkip: true,
                            },
                        },
                        y: {
                            beginAtZero: true,
                            ticks: {
                                precision: 0,
                            },
                        },
                    },
                },
            });
        }

        function renderReport(data) {
            renderSummary(data);
            renderTable(data);
            renderChart(data);
        }

        async function loadMembers() {
            try {
                const response = await fetch("/api/team/members", { headers: authHeaders });
                if (!response.ok) {
                    throw new Error("Unable to load team members.");
                }
                const payload = await response.json();
                const members = Array.isArray(payload) ? payload : [];
                state.members = members;
                memberSelect.innerHTML = "";
                const allOption = document.createElement("option");
                allOption.value = "all";
                allOption.textContent = "All team members";
                memberSelect.appendChild(allOption);
                members
                    .slice()
                    .sort((a, b) => (a.name || "").localeCompare(b.name || ""))
                    .forEach((member) => {
                        const option = document.createElement("option");
                        option.value = String(member.id);
                        option.textContent = member.name || `Team member ${member.id}`;
                        memberSelect.appendChild(option);
                    });
                memberSelect.value = "all";
            } catch (error) {
                console.error("Failed to load team members", error);
                setMessage(messageElement, error?.message || "Unable to load team members.", "error");
                throw error;
            }
        }

        async function fetchReport() {
            const startValue = startInput.value?.trim();
            const endValue = endInput.value?.trim();
            if (!startValue || !endValue) {
                setMessage(messageElement, "Select both start and end dates to load the report.");
                return;
            }

            const params = new URLSearchParams();
            params.set("startDate", startValue);
            params.set("endDate", endValue);
            const memberValue = memberSelect.value;
            if (memberValue && memberValue !== "all") {
                params.append("teamMemberId", memberValue);
            }

            setMessage(messageElement, "Loading responsibility report…");
            updateDownloadButtons(downloads, false);

            try {
                const response = await fetch(
                    `/api/responsibilities/reports/member-summary?${params.toString()}`,
                    { headers: authHeaders }
                );
                const payload = await response.json();
                if (!response.ok) {
                    const errorMessage = payload?.msg || "Unable to load responsibility report.";
                    throw new Error(errorMessage);
                }
                state.data = payload;
                renderReport(payload);
                const summaryText = `Period: ${formatDateDisplay(payload.startDate)} – ${formatDateDisplay(
                    payload.endDate
                )} | Team members: ${getSelectionLabel()} | Occurrences: ${payload.totalOccurrences || 0}`;
                downloads.forEach((button) => {
                    if (!button) {
                        return;
                    }
                    button.dataset.reportSummary = summaryText;
                });
                setMessage(messageElement, "", "");
                updateDownloadButtons(downloads, (payload.totalOccurrences || 0) > 0);
            } catch (error) {
                console.error("Failed to load responsibility report", error);
                state.data = null;
                renderReport(null);
                setMessage(messageElement, error?.message || "Unable to load responsibility report.", "error");
            }
        }

        form.addEventListener("submit", (event) => {
            event.preventDefault();
            fetchReport();
        });

        startInput.addEventListener("change", () => {
            if (endInput.value) {
                fetchReport();
            }
        });

        endInput.addEventListener("change", () => {
            if (startInput.value) {
                fetchReport();
            }
        });

        memberSelect.addEventListener("change", () => {
            fetchReport();
        });

        downloads.forEach((button) => {
            if (!button) {
                return;
            }
            button.addEventListener("click", () => {
                if (!state.data) {
                    return;
                }
                const summaryText = button.dataset.reportSummary || "";
                const start = state.data.startDate || "";
                const end = state.data.endDate || "";
                const baseName = `responsibility-report-${start || "start"}-${end || "end"}`;
                const format = button.dataset.reportDownload;
                if (format === "excel") {
                    downloadExcel(state.data, `${baseName}.xlsx`);
                } else if (format === "pdf") {
                    downloadPdf(state.data, `${baseName}.pdf`, summaryText);
                } else if (format === "png") {
                    downloadPng(state.chart, `${baseName}.png`);
                }
            });
        });

        const today = new Date();
        if (!endInput.value) {
            endInput.value = toIsoDateString(today);
        }
        if (!startInput.value) {
            const start = new Date(today.getTime() - 29 * 24 * 60 * 60 * 1000);
            startInput.value = toIsoDateString(start);
        }

        (async () => {
            try {
                await loadMembers();
            } catch (error) {
                console.error("Unable to initialise responsibility report", error);
            } finally {
                fetchReport();
            }
        })();
    }

    window.initResponsibilityReport = initResponsibilityReport;
})();
