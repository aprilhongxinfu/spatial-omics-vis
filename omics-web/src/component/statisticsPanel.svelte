<script>
    import { onMount, tick } from "svelte";
    import Plotly from "plotly.js-dist-min";

    export let spotMetricsData = [];

    const metrics = [
        "nCount_Spatial",
        "nFeature_Spatial",
        "percent_mito",
        "percent_ribo",
    ];

    let violinDiv;
    let resizeCleanup = null;

    $: if (spotMetricsData && spotMetricsData.length && violinDiv) {
        tick().then(() => drawFacetViolins(spotMetricsData));
    }

    onMount(() => {
        if (spotMetricsData && spotMetricsData.length && violinDiv) {
            drawFacetViolins(spotMetricsData);
        }

        return () => {
            if (resizeCleanup) resizeCleanup();
        };
    });

    function drawFacetViolins(data) {
        if (!violinDiv || !violinDiv.isConnected) {
            console.warn("❌ violinDiv not ready for Plotly render");
            return;
        }

        const traces = [];
        for (let i = 0; i < metrics.length; i++) {
            const metric = metrics[i];
            const axisSuffix = i === 0 ? "" : i + 1;

            const metricData = data.filter((d) => d.metric === metric);

            traces.push({
                type: "violin",
                x: Array(metricData.length).fill("All Spots"),
                y: metricData.map((d) => d.value),
                customdata: metricData.map((d) => d.barcode),
                hovertemplate: "Barcode: %{customdata}<extra></extra>",
                name: metric,
                box: { visible: true },
                meanline: { visible: true },
                points: false,
                jitter: 0.4,
                pointpos: 0,
                side: "both",
                marker: {
                    color: "black",
                    size: 2.5,
                },
                fillcolor: "rgba(100, 116, 139, 0.6)", // slate-500 with opacity
                line: {
                    color: "rgba(100, 116, 139, 1)", // slate-500
                    width: 1,
                },
                xaxis: `x${axisSuffix}`,
                yaxis: `y${axisSuffix}`,
                showlegend: false,
            });
        }

        const layout = {
            grid: { rows: 2, columns: 2, pattern: "independent" },
            margin: { t: 15, l: 35, r: 15, b: 20 },
            title: "",
            showlegend: false,
            height: 280,
            annotations: metrics.map((metric, i) => {
                const col = i % 2;
                const row = Math.floor(i / 2);

                return {
                    text: metric,
                    font: { size: 11, color: "#333" },
                    showarrow: false,
                    xref: "paper",
                    yref: "paper",
                    x: col * 0.5 + 0.25,
                    y: 1 - row * 0.5 - 0.05,
                    xanchor: "center",
                    yanchor: "bottom",
                };
            }),
        };

        for (let i = 0; i < metrics.length; i++) {
            const idx = i === 0 ? "" : i + 1;
            layout[`xaxis${idx}`] = {
                title: "",
                ticktext: [],
                tickvals: [],
                showticklabels: false,
            };
            layout[`yaxis${idx}`] = { 
                title: metrics[i],
                titlefont: { size: 10 }
            };
        }

        Plotly.newPlot(violinDiv, traces, layout, {
            scrollZoom: true,
            responsive: true,
            useResizeHandler: true,
            displaylogo: false,
            modeBarButtons: [["pan2d", "resetScale2d", "toImage"]],
        });

        if (resizeCleanup) resizeCleanup();
        resizeCleanup = observeResize(violinDiv, () => {
            if (violinDiv && violinDiv.isConnected) {
                Plotly.Plots.resize(violinDiv);
            }
        });
    }

    function observeResize(dom, callback) {
        if (!dom) return null;
        const ro = new ResizeObserver(() => {
            if (dom && dom.isConnected && callback) {
                callback();
            }
        });
        ro.observe(dom);
        return () => ro.disconnect();
    }
</script>

{#if spotMetricsData && spotMetricsData.length}
    <div class="space-y-1">
        <div class="text-xs font-semibold text-gray-700">Statistics</div>
        <div bind:this={violinDiv} class="-mx-2" style="max-height: 280px; height: 280px;"></div>
    </div>
{/if}
