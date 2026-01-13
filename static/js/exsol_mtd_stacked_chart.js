const widget = document.querySelector("[data-exsol-stacked-widget]");

if (widget) {
    const token = localStorage.getItem("samprox_token");
    const headers = token ? { Authorization: `Bearer ${token}` } : {};

    const startInput = widget.querySelector("#exsol-stacked-start");
    const endInput = widget.querySelector("#exsol-stacked-end");
    const itemsSelect = widget.querySelector("#exsol-stacked-items");
    const metricSelect = widget.querySelector("#exsol-stacked-metric");
    const compareCheckbox = widget.querySelector("#exsol-stacked-compare");
    const applyButton = widget.querySelector("#exsol-stacked-apply");
    const statusEl = widget.querySelector("[data-exsol-stacked-status]");
    const chartCanvas = widget.querySelector("#exsol-stacked-chart");
    const salesValueEl = widget.querySelector("#exsol-mtd-sales");
    const qtyValueEl = widget.querySelector("#exsol-mtd-qty");

    let chartInstance = null;

    const amountFormatter = new Intl.NumberFormat("en-LK", {
        minimumFractionDigits: 0,
        maximumFractionDigits: 2,
    });
    const qtyFormatter = new Intl.NumberFormat("en-LK", { maximumFractionDigits: 0 });
    const kpiAmountFormatter = new Intl.NumberFormat("en-LK", { maximumFractionDigits: 0 });

    const formatDate = (value) => {
        const year = value.getFullYear();
        const month = String(value.getMonth() + 1).padStart(2, "0");
        const day = String(value.getDate()).padStart(2, "0");
        return `${year}-${month}-${day}`;
    };

    const setStatus = (state, message) => {
        if (!statusEl || !chartCanvas) return;
        if (state === "none") {
            statusEl.hidden = true;
            chartCanvas.hidden = false;
            return;
        }
        statusEl.textContent = message;
        statusEl.hidden = false;
        chartCanvas.hidden = state === "empty" || state === "error";
    };

    const colorForIndex = (index, alpha) => {
        const hue = (index * 47) % 360;
        return `hsla(${hue}, 70%, 50%, ${alpha})`;
    };

    const buildDatasets = (labels, itemCodes, series, compare) => {
        const datasets = [];
        itemCodes.forEach((code, idx) => {
            const currentData = labels.map((label) => series.current?.[label]?.[code] ?? 0);
            datasets.push({
                label: code,
                data: currentData,
                stack: "current",
                backgroundColor: colorForIndex(idx, 0.8),
                borderWidth: 1,
            });
            if (compare) {
                const previousData = labels.map((label) => series.previous?.[label]?.[code] ?? 0);
                datasets.push({
                    label: code,
                    data: previousData,
                    stack: "previous",
                    backgroundColor: colorForIndex(idx, 0.35),
                    borderWidth: 1,
                });
            }
        });
        return datasets;
    };

    const buildChart = (labels, datasets, metric) => {
        const yTitle = metric === "qty" ? "Quantity (Units)" : "Sales Amount (LKR)";
        if (!chartInstance) {
            const context = chartCanvas.getContext("2d");
            chartInstance = new Chart(context, {
                type: "bar",
                data: { labels, datasets },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    scales: {
                        x: { stacked: true },
                        y: {
                            stacked: true,
                            beginAtZero: true,
                            title: { display: true, text: yTitle },
                            ticks: {
                                callback: (value) => {
                                    if (metric === "qty") {
                                        return qtyFormatter.format(value);
                                    }
                                    return amountFormatter.format(value);
                                },
                            },
                        },
                    },
                    plugins: {
                        tooltip: {
                            callbacks: {
                                label: (context) => {
                                    const itemCode = context.dataset.label || "";
                                    const period = context.dataset.stack === "previous" ? "Previous" : "Current";
                                    const rawValue = context.raw ?? 0;
                                    const formattedValue =
                                        metric === "qty"
                                            ? qtyFormatter.format(rawValue)
                                            : `LKR ${amountFormatter.format(rawValue)}`;
                                    return `${itemCode} (${period}): ${formattedValue}`;
                                },
                            },
                        },
                        legend: {
                            labels: {
                                filter: (item, chartData) =>
                                    chartData.datasets[item.datasetIndex]?.stack !== "previous",
                            },
                        },
                    },
                },
            });
        } else {
            chartInstance.data.labels = labels;
            chartInstance.data.datasets = datasets;
            if (chartInstance.options.scales?.y?.title) {
                chartInstance.options.scales.y.title.text = yTitle;
            }
            chartInstance.update();
        }
    };

    const populateItems = (items) => {
        if (!itemsSelect) return;
        itemsSelect.innerHTML = "";
        items.forEach((item) => {
            const option = document.createElement("option");
            option.value = item.code;
            option.textContent = item.name ? `${item.code} — ${item.name}` : item.code;
            itemsSelect.appendChild(option);
        });
    };

    const getSelectedItems = () => Array.from(itemsSelect.selectedOptions).map((option) => option.value);

    const setKpiValues = (salesAmount, waterPumpQty) => {
        if (salesValueEl) {
            const amount = Number.isFinite(salesAmount) ? salesAmount : 0;
            salesValueEl.textContent = `Rs. ${kpiAmountFormatter.format(amount)}`;
        }
        if (qtyValueEl) {
            const qty = Number.isFinite(waterPumpQty) ? waterPumpQty : 0;
            qtyValueEl.textContent = `${kpiAmountFormatter.format(qty)} Units`;
        }
    };

    const loadItems = async () => {
        try {
            const resp = await fetch("/api/exsol/inventory-items/codes", { headers });
            if (!resp.ok) {
                throw new Error(`Failed to load items (${resp.status})`);
            }
            const data = await resp.json();
            populateItems(Array.isArray(data) ? data : []);
        } catch (error) {
            console.error("Unable to load Exsol items", error);
        }
    };

    const loadSummary = async () => {
        if (!startInput.value || !endInput.value) {
            setKpiValues(0, 0);
            return;
        }

        const params = new URLSearchParams({
            start_date: startInput.value,
            end_date: endInput.value,
        });

        const selectedItems = getSelectedItems();
        if (selectedItems.length) {
            selectedItems.forEach((code) => params.append("item_codes", code));
        }

        try {
            const resp = await fetch(`/api/exsol/sales/mtd-summary?${params.toString()}`, { headers });
            if (!resp.ok) {
                throw new Error(`Failed to load KPI summary (${resp.status})`);
            }
            const data = await resp.json();
            setKpiValues(data?.sales_amount_lkr ?? 0, data?.water_pump_qty ?? 0);
        } catch (error) {
            console.error("Unable to load Exsol KPI summary", error);
            setKpiValues(0, 0);
        }
    };

    const loadChartData = async () => {
        if (!startInput.value || !endInput.value) {
            setStatus("empty", "Select a start and end date.");
            return;
        }

        const params = new URLSearchParams({
            start: startInput.value,
            end: endInput.value,
            metric: metricSelect.value,
            compare: compareCheckbox.checked ? "1" : "0",
        });

        const selectedItems = getSelectedItems();
        if (selectedItems.length) {
            params.set("items", selectedItems.join(","));
        }

        setStatus("loading", "Loading…");
        applyButton.disabled = true;

        try {
            const resp = await fetch(`/api/exsol/sales/dashboard/stacked-sales?${params.toString()}`, {
                headers,
            });
            if (!resp.ok) {
                throw new Error(`Failed to load chart (${resp.status})`);
            }
            const data = await resp.json();
            const labels = data.labels || [];
            const itemCodes = data.item_codes || [];
            if (!labels.length || !itemCodes.length) {
                setStatus("empty", "No data for selected filters.");
                buildChart([], [], metricSelect.value);
                return;
            }

            const datasets = buildDatasets(labels, itemCodes, data.series || {}, data.compare);
            setStatus("none", "");
            buildChart(labels, datasets, metricSelect.value);
        } catch (error) {
            console.error("Unable to load Exsol stacked chart", error);
            setStatus("error", "Unable to load data.");
        } finally {
            applyButton.disabled = false;
        }
    };

    const setDefaultDates = () => {
        const today = new Date();
        const firstDay = new Date(today.getFullYear(), today.getMonth(), 1);
        startInput.value = formatDate(firstDay);
        endInput.value = formatDate(today);
    };

    setDefaultDates();
    loadItems().then(() => {
        loadChartData();
        loadSummary();
    });

    applyButton.addEventListener("click", () => {
        loadChartData();
        loadSummary();
    });
}
