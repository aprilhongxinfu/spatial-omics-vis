<script>
    import { createEventDispatcher } from "svelte";

    export let allLog = [];
    export let hoveredBarcode = { barcode: -1, from: "", persistent: false };
    export let spatialData = [];
    export let clusterColorScale;

    const dispatch = createEventDispatcher();

    function getClusterColor(cluster) {
        if (!cluster || typeof clusterColorScale !== "function") return "";
        try {
            return clusterColorScale(cluster) ?? "";
        } catch (err) {
            return "";
        }
    }

    let expandedIndex = null;

    $: normalizedLog = Array.isArray(allLog) ? allLog : [];

    function toggleRow(index) {
        expandedIndex = expandedIndex === index ? null : index;
    }

    function normalizeBarcodes(trace) {
        if (!trace) return [];
        const { customdata, text, hovertext } = trace;
        if (Array.isArray(customdata) && customdata.length) return customdata;
        if (Array.isArray(text) && text.length) return text;
        if (Array.isArray(hovertext) && hovertext.length) return hovertext;
        if (typeof customdata === "string") return [customdata];
        if (typeof text === "string") return [text];
        if (typeof hovertext === "string") return [hovertext];
        return [];
    }

    function handleLogClick(row, event) {
        if (!row || !row.barcode) return;
        
        // Find the spot in spatialData to get coordinates
        let spotInfo = {
            barcode: row.barcode,
            cluster: row.new_cluster?.replace(/^cluster\s*/i, "") ?? row.new_cluster,
            x: null,
            y: null,
        };
        
        if (Array.isArray(spatialData)) {
            for (const trace of spatialData) {
                const barcodes = normalizeBarcodes(trace);
                const xs = Array.isArray(trace?.x) ? trace.x : [];
                const ys = Array.isArray(trace?.y) ? trace.y : [];
                
                const idx = barcodes.findIndex(bc => `${bc}`.trim() === `${row.barcode}`.trim());
                if (idx !== -1) {
                    spotInfo.x = Number.isFinite(xs[idx]) ? xs[idx] : null;
                    spotInfo.y = Number.isFinite(ys[idx]) ? ys[idx] : null;
                    if (!spotInfo.cluster) {
                        spotInfo.cluster = trace?.name?.replace(/^cluster\s*/i, "") ?? trace?.name;
                    }
                    break;
                }
            }
        }
        
        // Dispatch spotClick event similar to plot click
        dispatch("spotClick", {
            info: spotInfo,
            lassoSelected: false,
            previewUrl: null,
            pointer: {
                clientX: event?.clientX ?? 0,
                clientY: event?.clientY ?? 0,
            },
        });
    }

    function isLogSelected(row) {
        if (!row || !row.barcode) return false;
        const selected = hoveredBarcode?.barcode;
        if (selected === undefined || selected === null) return false;
        return `${selected}`.trim() === `${row.barcode}`.trim() && hoveredBarcode?.persistent;
    }
</script>

<div class="flex flex-col h-full overflow-hidden">
    {#if normalizedLog.length === 0}
        <div class="flex-1 flex items-center justify-center text-[11px] text-slate-500">
            No log entries.
        </div>
    {:else}
        <div class="flex-1 overflow-auto">
            <table class="table-auto w-full text-[11px]">
                <thead class="sticky top-0 bg-slate-100 text-slate-600">
                    <tr>
                        <th class="text-left px-2 py-1 font-semibold">Barcode</th>
                        <th class="text-left px-2 py-1 font-semibold w-14">Prev</th>
                        <th class="text-left px-2 py-1 font-semibold w-14">New</th>
                    </tr>
                </thead>
                <tbody>
                    {#each normalizedLog as row, i}
                        {@const isSelected = isLogSelected(row)}
                        <tr
                            class="cursor-pointer transition-colors border-b border-slate-100/50"
                            class:bg-blue-50={isSelected}
                            class:hover:bg-slate-50={!isSelected}
                            class:hover:bg-blue-100={isSelected}
                            on:click={(e) => {
                                handleLogClick(row, e);
                                toggleRow(i);
                            }}
                        >
                            <td class="px-2 py-1 font-mono text-slate-700 truncate max-w-[120px]" title={row.barcode}>{row.barcode}</td>
                            <td class="px-2 py-1 font-medium" style={`color:${getClusterColor(row.old_cluster)};`}>{row.old_cluster}</td>
                            <td class="px-2 py-1 font-medium" style={`color:${getClusterColor(row.new_cluster)};`}>{row.new_cluster}</td>
                        </tr>
                        {#if expandedIndex === i}
                            <tr class="bg-slate-50/80 text-[10px]">
                                <td colspan="3" class="px-2 py-1.5 space-y-0.5">
                                    <div>
                                        <span class="font-medium text-slate-500">Comment:</span>
                                        <span class="ml-1 text-slate-600">{row.comment || "-"}</span>
                                    </div>
                                    <div>
                                        <span class="font-medium text-slate-500">Time:</span>
                                        <span class="ml-1 text-slate-600">{row.updated_at}</span>
                                    </div>
                                </td>
                            </tr>
                        {/if}
                    {/each}
                </tbody>
            </table>
        </div>
    {/if}
</div>

