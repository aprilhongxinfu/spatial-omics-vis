<script>
    import { createEventDispatcher, tick } from "svelte";
    import { Info } from "@lucide/svelte";

    export let clickedInfo = [];
    export let clusterColorScale;
    export let spatialData = [];
    export let baseApi = "";
    export let currentSlice = "";
    export let initCurrentMethod = "";
    export let initCurrentClusterResultId = "";
    export let previewUrl = null;
    export let lassoSelected = true;
    export let hoveredBarcode = { barcode: -1, from: "spotPlot", persistent: false };

    const dispatch = createEventDispatcher();

    const KNOWN_FIELDS = new Set([
        "barcode",
        "cluster",
        "original_cluster",
        "old_cluster",
        "new_cluster",
        "comment",
        "updated_at",
        "changed",
    ]);

    // Reclustering metric fields to filter out in manual mode
    const RECLUSTERING_METRIC_FIELDS = new Set([
        "confidence",
        "p_value",
        "mapping_source",
        "relationship",
        "max_prob",
        "hard_label",
        "silhouette_score",
        "calinski_harabasz",
        "davies_bouldin_inv",
        "log_likelihood",
        "spatial_continuity",
        "boundary_consistency",
    ]);

    $: barcodeClusterMap = buildBarcodeClusterMap(spatialData);

    // Extract available clusters from spatialData
    $: availableClusters = extractAvailableClusters(spatialData);
    
    // Lasso statistics
    let lassoStats = null;
    let lassoStatsLoading = false;
    let lassoStatsError = null;
    // Cache key to avoid refetching same selection
    let lastLassoStatsKey = null;
    let showStats = false;
    let showHistoryHint = false;
    let showCorrectionHint = false;
    
    // Fetch lasso statistics when rows change
    async function fetchLassoStatistics() {
        if (!baseApi || !currentSlice || rows.length === 0) {
            lassoStats = null;
            lastLassoStatsKey = null;
            return;
        }
        
        const barcodes = rows.map(r => r.barcode);
        const currentKey = JSON.stringify({
            slice_id: currentSlice,
            cluster_result_id: initCurrentClusterResultId || "default",
            barcodes,
        });
        
        // If the selection hasn't changed and we already have stats, skip refetch
        if (currentKey === lastLassoStatsKey && lassoStats) {
            return;
        }
        lastLassoStatsKey = currentKey;
        
        lassoStatsLoading = true;
        lassoStatsError = null;
        
        try {
            const res = await fetch(`${baseApi}/lasso-statistics`, {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                },
                body: JSON.stringify({
                    barcodes,
                    slice_id: currentSlice,
                    cluster_result_id: initCurrentClusterResultId || "default"
                }),
            });
            if (!res.ok) {
                throw new Error(`Failed to fetch statistics (${res.status})`);
            }
            lassoStats = await res.json();
        } catch (err) {
            lassoStatsError = err.message;
            lassoStats = null;
        } finally {
            lassoStatsLoading = false;
        }
    }
    
    // Reactively fetch stats when rows change
    $: if (rows.length > 0 && baseApi && currentSlice) {
        fetchLassoStatistics();
    } else {
        lassoStats = null;
        lastLassoStatsKey = null;
    }
    
    // Cluster history across other results
    let clusterHistory = new Map(); // Map<barcode, [{result_id, method, cluster}]>
    let clusterHistoryLoading = false;
    
    async function fetchClusterHistory() {
        if (!baseApi || !currentSlice || rows.length === 0) {
            clusterHistory = new Map();
            return;
        }
        
        clusterHistoryLoading = true;
        
        try {
            const barcodes = rows.map(r => r.barcode);
            const res = await fetch(`${baseApi}/spots-cluster-history`, {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                },
                body: JSON.stringify({
                    barcodes,
                    slice_id: currentSlice,
                    current_cluster_result_id: initCurrentClusterResultId || "default"
                }),
            });
            if (!res.ok) {
                throw new Error(`Failed to fetch cluster history (${res.status})`);
            }
            const data = await res.json();
            clusterHistory = new Map(Object.entries(data.history || {}));
        } catch (err) {
            console.error("Error fetching cluster history:", err);
            clusterHistory = new Map();
        } finally {
            clusterHistoryLoading = false;
        }
    }
    
    // Track barcodes to detect changes (not just length)
    $: barcodesKey = rows.length > 0 
        ? rows.map(r => r.barcode).sort().join(',')
        : '';
    
    // Reactively fetch cluster history (available both before and after recluster)
    // Use barcodesKey to ensure refetch when barcodes change, not just length
    $: if (barcodesKey && baseApi && currentSlice) {
        fetchClusterHistory();
    } else {
        clusterHistory = new Map();
    }
    
    // Helper to summarize cluster history for a barcode
    function summarizeClusterHistory(barcode) {
        const history = clusterHistory.get(barcode);
        if (!history || history.length === 0) return null;
        
        // Filter out unknown method entries
        const validHistory = history.filter(h => h.method && h.method !== "unknown");
        if (validHistory.length === 0) return null;
        
        // Count occurrences of each cluster
        const clusterCounts = {};
        validHistory.forEach(h => {
            const c = h.cluster;
            clusterCounts[c] = (clusterCounts[c] || 0) + 1;
        });
        
        // Sort by count descending
        const sorted = Object.entries(clusterCounts)
            .sort((a, b) => b[1] - a[1])
            .map(([cluster, count]) => ({ cluster, count, total: validHistory.length }));
        
        return sorted;
    }
    
    function extractAvailableClusters(data) {
        if (!Array.isArray(data) || !data.length) return [];
        const clusters = new Set();
        data.forEach((trace) => {
            if (!trace) return;
            const clusterLabel =
                trace.cluster ??
                trace.cluster_name ??
                trace.name ??
                trace.id ??
                "";
            if (clusterLabel) {
                clusters.add(`${clusterLabel}`);
            }
        });
        return Array.from(clusters).sort((a, b) => {
            // Try to sort numerically if possible
            const numA = parseInt(a, 10);
            const numB = parseInt(b, 10);
            if (!isNaN(numA) && !isNaN(numB)) return numA - numB;
            return a.localeCompare(b);
        });
    }

    function buildBarcodeClusterMap(data) {
        if (!Array.isArray(data) || !data.length) return new Map();
        const map = new Map();

        data.forEach((trace) => {
            if (!trace) return;
            const clusterLabel =
                trace.cluster ??
                trace.cluster_name ??
                trace.name ??
                trace.id ??
                "";

            const rawBarcodes =
                trace.customdata ??
                trace.text ??
                trace.hovertext ??
                trace.barcode ??
                [];

            const normalizedBarcodes = Array.isArray(rawBarcodes)
                ? rawBarcodes
                : typeof rawBarcodes === "string"
                  ? [rawBarcodes]
                  : [];

            normalizedBarcodes.forEach((barcode) => {
                if (barcode == null) return;
                const key = `${barcode}`.trim();
                if (!key) return;
                if (!map.has(key)) {
                    map.set(key, `${clusterLabel}`);
                }
            });
        });

        return map;
    }

    function toRow(entry, index) {
        if (entry == null) return null;

        if (typeof entry === "string" || typeof entry === "number") {
            const barcode = `${entry}`.trim();
            const baseCluster = barcodeClusterMap.get(barcode) ?? null;
            // Current Cluster 始终显示 original cluster
            const savedOriginalCluster = originalClusterMap.get(barcode);
            const currentCluster = savedOriginalCluster ?? baseCluster;
            return {
                uid: `${index}:${entry}`,
                index: index + 1,
                barcode,
                cluster: currentCluster,
            };
        }

        if (typeof entry === "object") {
            const barcode =
                entry.barcode ??
                entry.Barcode ??
                entry.id ??
                entry?.barcode_id ??
                `${index + 1}`;
            const normalizedBarcode = `${barcode}`.trim();

            const details = Object.entries(entry).reduce((acc, [key, value]) => {
                if (!KNOWN_FIELDS.has(key) && key !== "flowInfo") {
                    acc[key] = value;
                }
                return acc;
            }, {});

            const fallbackCluster =
                barcodeClusterMap.get(normalizedBarcode) ?? null;
            // 从 Sankey 跳转来的数据可能有 flowInfo.fromCluster
            const flowInfoCluster = entry.flowInfo?.fromCluster ?? null;
            const baseCluster =
                entry.cluster ??
                entry.current_cluster ??
                entry.cluster_id ??
                flowInfoCluster ??  // 优先使用 flowInfo 中的 fromCluster
                fallbackCluster;
            
            // originalCluster: 优先使用重聚类结果返回的original_cluster字段
            // 如果没有，则使用保存的重聚类前的cluster值（从spatialData中读取的）
            const savedOriginalCluster = originalClusterMap.get(normalizedBarcode);
            const hasReclusterResult = entry.new_cluster != null;
            // 重聚类后，优先使用返回的original_cluster，其次使用保存的原始值
            // 不要使用currentCluster作为fallback，因为它可能已被自动重聚类影响
            const originalCluster = hasReclusterResult 
                ? (entry.original_cluster ?? savedOriginalCluster ?? fallbackCluster)  // 重聚类后，优先使用返回的original_cluster
                : null;  // 重聚类前，不显示original
            
            // Current Cluster 始终显示 original cluster，不管在自动还是手动模式
            // 优先使用重聚类结果返回的original_cluster，其次使用保存的原始值
            // 如果都没有，使用entry中的cluster字段（应该是初始值），最后才使用fallback
            const currentCluster = originalCluster ?? savedOriginalCluster ?? 
                (entry.cluster ?? entry.current_cluster ?? flowInfoCluster ?? fallbackCluster);
            
            // Extract flow info if present (from comparison view click)
            const flowInfo = entry.flowInfo ?? null;
            
            return {
                uid: `${index}:${barcode}`,
                index: index + 1,
                barcode: normalizedBarcode,
                cluster: currentCluster, // 当前簇（重聚类前显示这个）
                originalCluster: originalCluster, // 重聚类前的簇（重聚类后显示这个，优先使用保存的值）
                newCluster: entry.new_cluster ?? null, // 重聚类后的新簇
                comment: entry.comment ?? null,
                updatedAt: entry.updated_at ?? null,
                hasChanged: entry.changed ?? false,
                flowInfo, // Flow info from comparison view
                details,
            };
        }

        return null;
    }

    // 保存重聚类前的cluster信息
    let originalClusterMap = new Map();
    
    $: if (Array.isArray(clickedInfo)) {
        // 在重聚类前，保存每个barcode的原始cluster
        clickedInfo.forEach((entry) => {
            let barcode = null;
            if (typeof entry === "string" || typeof entry === "number") {
                barcode = `${entry}`.trim();
            } else if (typeof entry === "object" && entry) {
                barcode = (entry.barcode ?? entry.Barcode ?? entry.id ?? entry?.barcode_id ?? "").toString().trim();
            }
            
            if (barcode && !originalClusterMap.has(barcode)) {
                const cluster = barcodeClusterMap.get(barcode);
                if (cluster) {
                    originalClusterMap.set(barcode, cluster);
                }
            }
        });
    }
    
    $: rows = Array.isArray(clickedInfo)
        ? clickedInfo
              .map((entry, index) => {
                  const row = toRow(entry, index);
                  if (!row || row.barcode === undefined) return null;

                  // In manual mode, override newCluster to only show manual selections
                  // and ignore automatic re-clustering results
                  if (correctionMode === "manual") {
                      const original =
                          row.originalCluster ??
                          row.cluster ??
                          barcodeClusterMap.get(row.barcode) ??
                          "";
                      const manualNew = manualNewClusters.get(row.barcode) ?? null;
                      // 在手动模式下，newCluster只显示用户手动选择的值，不显示自动重聚类的结果
                      row.newCluster = manualNew;
                      row.hasChanged =
                          !!manualNew && manualNew !== "" && manualNew !== original;
                  }

                  return row;
              })
              .filter((row) => row && row.barcode !== undefined)
        : [];

    $: hasOriginalCluster = rows.some((row) => row.originalCluster);
    $: hasNewCluster = rows.some((row) => row.newCluster);
    $: hasCurrentCluster = rows.some((row) => row.cluster);
    $: hasComment = rows.some((row) => row.comment);
    $: hasUpdatedAt = rows.some((row) => row.updatedAt);
    $: hasDetails = rows.some(
        (row) => row.details && Object.keys(row.details).length,
    );
    $: hasFlowInfo = rows.some((row) => row.flowInfo);
    $: flowInfo = hasFlowInfo && rows.length > 0 ? rows[0].flowInfo : null;

    let expandedUid = null;
    let geneExpressionData = new Map(); // Map<barcode, {loading, error, data}>
    let tableContainer; // Reference to the scrollable table container
    let highlightedRowUid = null; // Track currently highlighted row
    
    async function fetchGeneExpression(barcode) {
        if (!baseApi || !currentSlice) return;
        if (geneExpressionData.has(barcode) && !geneExpressionData.get(barcode).error) return;
        
        // Set loading state
        geneExpressionData.set(barcode, { loading: true, error: null, data: null });
        geneExpressionData = geneExpressionData;
        
        try {
            const params = new URLSearchParams({
                barcode,
                slice_id: currentSlice,
                top_n: "15"
            });
            const res = await fetch(`${baseApi}/spot-gene-expression?${params}`);
            if (!res.ok) {
                throw new Error(`Failed to fetch gene expression (${res.status})`);
            }
            const data = await res.json();
            geneExpressionData.set(barcode, { loading: false, error: null, data });
            geneExpressionData = geneExpressionData;
        } catch (err) {
            geneExpressionData.set(barcode, { loading: false, error: err.message, data: null });
            geneExpressionData = geneExpressionData;
        }
    }

    function toggleRow(row) {
        if (expandedUid === row.uid) {
            expandedUid = null;
        } else {
            expandedUid = row.uid;
            // Fetch gene expression data when expanding:
            // - Always fetch in manual mode
            // - Always fetch in auto mode (even if there are reclustering results)
            fetchGeneExpression(row.barcode);
        }
    }

    // Track if user is hovering over table (to avoid auto-scroll when user is already looking at the row)
    let isHoveringTable = false;

    function handleHover(row) {
        isHoveringTable = true;
        dispatch("lassoHover", {
            barcode: row.barcode,
            cluster: row.cluster ?? row.originalCluster ?? "",
            newCluster: row.newCluster ?? null,  // null if not reclustered yet
        });
    }

    function resetHover() {
        isHoveringTable = false;
        dispatch("lassoHover", {
            barcode: "",
            newCluster: "",
        });
    }

    // Scroll to and highlight table row when hovering from UMAP or plot
    function scrollToRow(barcode) {
        if (!tableContainer || !barcode || barcode === -1 || barcode === "") {
            highlightedRowUid = null;
            return;
        }

        const normalizedBarcode = `${barcode}`.trim();
        const targetRow = rows.find((row) => `${row.barcode}`.trim() === normalizedBarcode);
        
        if (!targetRow) {
            highlightedRowUid = null;
            return;
        }

        // Update highlighted row immediately to show highlight
        highlightedRowUid = targetRow.uid;

        // Find the row element and scroll to it
        tick().then(() => {
            const rowElement = tableContainer?.querySelector(`tr[data-row-uid="${targetRow.uid}"]`);
            if (rowElement && tableContainer) {
                // Calculate scroll position to center the row
                const containerRect = tableContainer.getBoundingClientRect();
                const rowRect = rowElement.getBoundingClientRect();
                const scrollTop = tableContainer.scrollTop;
                const rowOffsetTop = rowRect.top - containerRect.top + scrollTop;
                const containerHeight = tableContainer.clientHeight;
                const rowHeight = rowRect.height;
                
                // Scroll to center the row in the container
                const targetScrollTop = rowOffsetTop - (containerHeight / 2) + (rowHeight / 2);
                
                tableContainer.scrollTo({
                    top: Math.max(0, targetScrollTop),
                    behavior: 'smooth'
                });
            }
        });
    }

    // React to hoveredBarcode changes from UMAP or plot
    // Only scroll if user is not already hovering over the table
    $: if (hoveredBarcode && !isHoveringTable) {
        const isFromUmapOrPlot = hoveredBarcode.from === "umap" || hoveredBarcode.from === "spotPlot";
        const hovered = hoveredBarcode.barcode;
        
        if (isFromUmapOrPlot && hovered && hovered !== -1 && hovered !== "") {
            scrollToRow(hovered);
        } else {
            // Clear highlight when hover ends or not from UMAP/plot
            highlightedRowUid = null;
        }
    }

    // Track accepted rows (local state, not submitted yet)
    let acceptedRows = new Set();
    
    function handleAcceptRow(row) {
        if (!row || !row.barcode) return;
        
        const originalCluster = row.originalCluster ?? row.cluster ?? "";
        const newCluster = row.newCluster ?? "";
        
        // Check if we're accepting or canceling
        const isCurrentlyAccepted = acceptedRows.has(row.uid);
        
        // Toggle acceptance state
        if (isCurrentlyAccepted) {
            acceptedRows.delete(row.uid);
        } else {
            acceptedRows.add(row.uid);
        }
        acceptedRows = acceptedRows; // Trigger reactivity
        
        // Dispatch preview update to update spatialData display in real-time (frontend only, no backend update)
        if (isCurrentlyAccepted) {
            // Cancel: move FROM newCluster BACK TO originalCluster
            dispatch("previewRecluster", {
                barcode: row.barcode,
                oldCluster: newCluster,
                newCluster: originalCluster,
            });
        } else {
            // Accept: move FROM originalCluster TO newCluster
        dispatch("previewRecluster", {
            barcode: row.barcode,
                oldCluster: originalCluster,
                newCluster: newCluster,
        });
        }
    }
    
    async function handleComplete() {
        console.log("🎯 Complete button clicked, accepted rows:", acceptedRows.size);
        
        if (acceptedRows.size === 0) {
            // No accepted changes, just close
            console.log("⚠️ No accepted changes, closing without submission");
            dispatch("completeRecluster", {});
            return;
        }
        
        // Get all accepted rows that have actual changes
        const acceptedRowsList = rows.filter(r => 
            acceptedRows.has(r.uid) && 
            r.newCluster && 
            r.newCluster !== r.originalCluster
        );
        
        console.log("📋 Accepted rows list:", acceptedRowsList);
        console.log("📋 Accepted rows details:", acceptedRowsList.map(r => ({
            barcode: r.barcode,
            originalCluster: r.originalCluster,
            newCluster: r.newCluster,
        })));
        
        if (acceptedRowsList.length === 0) {
            // No changes to submit, just close
            console.log("⚠️ No actual changes to submit, closing");
            acceptedRows = new Set();
            dispatch("completeRecluster", {});
            return;
        }
        
        // Submit all accepted changes sequentially to backend (database and adata)
        // Wait for each update to complete before submitting the next one to ensure plot updates properly
        console.log(`🚀 Submitting ${acceptedRowsList.length} accepted changes to backend...`);
        for (let i = 0; i < acceptedRowsList.length; i++) {
            const row = acceptedRowsList[i];
            const isLast = i === acceptedRowsList.length - 1;
            
            console.log(`📤 Submitting change ${i + 1}/${acceptedRowsList.length} to backend:`, {
                barcode: row.barcode,
                oldCluster: row.originalCluster ?? row.cluster ?? "",
                newCluster: row.newCluster ?? "",
                isLast,
            });
            
            // Dispatch acceptRecluster event which will call update-cluster API to update database and adata
            dispatch("acceptRecluster", {
                barcode: row.barcode,
                oldCluster: row.originalCluster ?? row.cluster ?? "",
                newCluster: row.newCluster ?? "",
                comment: "",
                remainingChangedCount: isLast ? 0 : acceptedRowsList.length - i - 1,
            });
            
            // Wait a bit between submissions to allow each update to process and plot to refresh
            if (!isLast) {
                await new Promise(resolve => setTimeout(resolve, 100));
            }
        }
        
        // Clear accepted rows
        acceptedRows = new Set();
        console.log("✅ All changes submitted to backend, dispatching complete event");
        
        // Dispatch complete event after a delay to allow all updates to process and plot to refresh
        setTimeout(() => {
            dispatch("completeRecluster", {});
        }, 200);
    }

    function getClusterColor(cluster) {
        if (!cluster || typeof clusterColorScale !== "function") return "";
        try {
            return clusterColorScale(cluster) ?? "";
        } catch (err) {
            console.warn("clusterColorScale error:", err);
            return "";
        }
    }

    let isSubmitting = false;
    let submitError = null;
    let submitSuccess = null;
    
    // Batch assignment state
    let selectedBatchCluster = "";
    // Manual per-row new cluster selections (barcode -> cluster)
    let manualNewClusters = new Map();
    // Snapshot of "current" clusters when entering manual mode (for table display)
    let manualInitialClusterMap = new Map();
    // Correction mode: "auto" (backend recluster) or "manual" (batch assign)
    let correctionMode = "auto";
    
    function handleBatchAssign() {
        if (!selectedBatchCluster || rows.length === 0) return;
        
        const targetCluster = selectedBatchCluster;
        const spotCount = rows.length;
        
        // In manual mode, do NOT modify clickedInfo or current clusters.
        // Just record manual new-cluster choices, mark accepted rows locally,
        // and update plot preview in real time.
        const changedUids = new Set();
        const nextManualNew = new Map(manualNewClusters);
        
        rows.forEach((row) => {
            if (!row || !row.barcode) return;
            const barcode = row.barcode;
            const original =
                row.originalCluster ?? row.cluster ?? barcodeClusterMap.get(barcode) ?? "";

            const previousManual = manualNewClusters.get(barcode) ?? null;
            const effectiveNew =
                targetCluster && targetCluster !== original ? targetCluster : null;

            if (effectiveNew) {
                nextManualNew.set(barcode, effectiveNew);
                changedUids.add(row.uid);
                
                // Update plot: move from previous (manual or original) to new cluster
                if (previousManual !== effectiveNew) {
                    dispatch("previewRecluster", {
                        barcode,
                        oldCluster: previousManual ?? original,
                        newCluster: effectiveNew,
                    });
                }
            } else {
                // No new cluster (same as original) – remove manual override and revert plot
                nextManualNew.delete(barcode);
                if (previousManual) {
                    dispatch("previewRecluster", {
                        barcode,
                        oldCluster: previousManual,
                        newCluster: original,
                    });
                }
            }
        });
        
        manualNewClusters = nextManualNew;
        acceptedRows = changedUids;
        
        // Reset the dropdown
        selectedBatchCluster = "";
        
        submitSuccess = `Batch assigned ${spotCount} spots to cluster ${targetCluster}.`;
        submitError = null;
    }

    async function handleRecluster() {
        if (!Array.isArray(clickedInfo) || clickedInfo.length === 0) return;
        if (!baseApi) {
            submitError = "Base API endpoint is not configured.";
            submitSuccess = null;
            return;
        }

        // 在自动重聚类前，确保保存每个barcode的原始cluster值
        // 这样即使spatialData被更新，我们也能在手动模式下恢复原始值
        clickedInfo.forEach((entry) => {
            let barcode = null;
            if (typeof entry === "string" || typeof entry === "number") {
                barcode = `${entry}`.trim();
            } else if (typeof entry === "object" && entry) {
                barcode = (entry.barcode ?? entry.Barcode ?? entry.id ?? entry?.barcode_id ?? "").toString().trim();
            }
            
            if (barcode && !originalClusterMap.has(barcode)) {
                const cluster = barcodeClusterMap.get(barcode);
                if (cluster) {
                    originalClusterMap.set(barcode, cluster);
                }
            }
        });

        isSubmitting = true;
        submitError = null;
        submitSuccess = null;

        try {
            const formData = new FormData();
            formData.append("slice_id", currentSlice ?? "");
            formData.append("barcode", JSON.stringify(clickedInfo));

            if (initCurrentMethod) {
                formData.append("method", initCurrentMethod);
            }
            if (initCurrentClusterResultId) {
                formData.append(
                    "cluster_result_id",
                    initCurrentClusterResultId,
                );
            }

            if (!lassoSelected && previewUrl) {
                const blob = toBlob(previewUrl);
                if (blob) {
                    formData.append("image", blob, "lasso-preview.png");
                }
            }

            const res = await fetch(`${baseApi}/recluster`, {
                method: "POST",
                body: formData,
            });

            if (!res.ok) {
                throw new Error(`Recluster failed (${res.status})`);
            }

            const data = await res.json();
            console.log("🔬 Recluster result:", data);
            console.log("📊 Recluster result details:", {
                totalSpots: Array.isArray(data) ? data.length : 0,
                isArray: Array.isArray(data),
                dataType: typeof data,
                firstItem: Array.isArray(data) && data.length > 0 ? data[0] : null,
            });
            
            if (Array.isArray(data) && data.length) {
                clickedInfo = data;
                console.log("✅ Updated clickedInfo with recluster results:", clickedInfo);
            }

            submitSuccess = `Reclustered ${Array.isArray(data) ? data.length : rows.length} spots successfully.`;
            dispatch("reclustered", {
                result: data,
            });
        } catch (error) {
            console.error("Recluster error:", error);
            submitError = error?.message ?? "Failed to submit recluster request.";
        } finally {
            isSubmitting = false;
        }
    }

    function handleManualSetCluster(row, newCluster) {
        if (!row || !row.barcode) return;
        const barcode = row.barcode;
        const original =
            row.originalCluster ?? row.cluster ?? barcodeClusterMap.get(barcode) ?? "";

        const previousManual = manualNewClusters.get(barcode) ?? null;
        const effectiveNew = newCluster && newCluster !== original ? newCluster : null;

        // Track manual selection for UI dropdown only (do not touch clickedInfo/current clusters)
        if (effectiveNew) {
            manualNewClusters.set(barcode, effectiveNew);
        } else {
            manualNewClusters.delete(barcode);
        }
        manualNewClusters = manualNewClusters;

        // Maintain acceptedRows set based on whether there is an effective change
        const uid = row.uid;
        if (effectiveNew) {
            acceptedRows.add(uid);
        } else {
            acceptedRows.delete(uid);
        }
        acceptedRows = acceptedRows;

        // Update plot preview in real time
        if (previousManual === effectiveNew) {
            // No effective change; do nothing
            return;
        }

        if (effectiveNew) {
            // Apply / change manual cluster: move from previous (manual or original) to new
            dispatch("previewRecluster", {
                barcode,
                oldCluster: previousManual ?? original,
                newCluster: effectiveNew,
            });
        } else if (previousManual) {
            // Cancel manual override: move back to original
            dispatch("previewRecluster", {
                barcode,
                oldCluster: previousManual,
                newCluster: original,
            });
        }
    }

    function toBlob(source) {
        if (!source) return null;
        if (source instanceof Blob) return source;
        if (typeof source === "string" && source.startsWith("data:image/")) {
            return base64ToBlob(source);
        }
        return null;
    }

    function base64ToBlob(base64Data, contentType = "image/png") {
        const byteCharacters = atob(base64Data.split(",")[1]);
        const byteNumbers = new Array(byteCharacters.length)
            .fill()
            .map((_, i) => byteCharacters.charCodeAt(i));
        const byteArray = new Uint8Array(byteNumbers);
        return new Blob([byteArray], { type: contentType });
    }
</script>

<div class="flex flex-col gap-3 h-full min-h-0 min-w-0 overflow-hidden">
    <header class="flex items-start justify-between gap-4 flex-wrap">
        <div class="flex flex-col gap-1 min-w-[180px]">
            <h3 class="text-base font-semibold text-slate-700 leading-tight">
                Lasso Selection
            </h3>
            <p class="text-xs text-slate-500">
                Hover a row to highlight the corresponding spot on the plot.
            </p>
        </div>
        <div class="flex flex-col items-end gap-1.5 justify-end flex-1">
            <!-- 第一行：模式选择 -->
            <div class="flex items-center justify-end gap-2">
                <span class="text-[10px] uppercase tracking-[0.16em] text-slate-400">
                    Correction mode
                </span>
                <span
                    class="inline-flex items-center justify-center text-slate-400 hover:text-slate-600 cursor-help relative"
                    aria-label="Correction mode help"
                    role="button"
                    tabindex="0"
                    on:mouseenter={() => (showCorrectionHint = true)}
                    on:mouseleave={() => (showCorrectionHint = false)}
                >
                    <Info size={12} />

                    {#if showCorrectionHint}
                        <div
                            class="absolute z-30 mt-8 right-0 w-64 max-w-[70vw] text-[11px] leading-snug text-slate-50 bg-slate-900/95 rounded-md shadow-lg px-2.5 py-1.5"
                        >
                            Auto: call backend reclustering and show suggested clusters
                            here for manual review. Manual: use batch assign to
                            manually set clusters before applying changes.
                        </div>
                    {/if}
                </span>
                <div class="inline-flex items-center rounded-md border border-slate-300 bg-white overflow-hidden text-[11px]">
                    <button
                        type="button"
                        class="px-2.5 py-1 border-r border-slate-200 {correctionMode === 'auto'
                            ? 'bg-slate-700 text-white'
                            : 'bg-white text-slate-700 hover:bg-slate-50'} disabled:opacity-50"
                        on:click={() => {
                            if (isSubmitting) return;

                            // When leaving manual mode, revert any manual preview changes on the plot
                            manualNewClusters.forEach((manualCluster, barcode) => {
                                const row =
                                    rows.find((r) => r.barcode === barcode) ?? null;
                                const original =
                                    row?.originalCluster ??
                                    row?.cluster ??
                                    barcodeClusterMap.get(barcode) ??
                                    "";

                                if (!original || !manualCluster) return;

                                dispatch("previewRecluster", {
                                    barcode,
                                    oldCluster: manualCluster,
                                    newCluster: original,
                                });
                            });

                            correctionMode = 'auto';
                            // Reset manual state when switching back to auto mode
                            acceptedRows = new Set();
                            manualNewClusters = new Map();
                            manualInitialClusterMap = new Map();
                        }}
                        disabled={isSubmitting}
                    >
                        Auto
                    </button>
                    <button
                        type="button"
                        class="px-2.5 py-1 {correctionMode === 'manual'
                            ? 'bg-slate-700 text-white'
                            : 'bg-white text-slate-700 hover:bg-slate-50'} disabled:opacity-50"
                        on:click={() => {
                            if (isSubmitting) return;
                            correctionMode = 'manual';
                            // 进入 manual 时记录一份原始 Cluster 的快照，用于表格展示
                            // 优先使用 originalCluster（自动重聚类前的值），如果没有则使用当前 cluster
                            // 这样可以避免自动重聚类结果影响手动模式的初始显示
                            manualInitialClusterMap = new Map();
                            rows.forEach((row) => {
                                if (!row || !row.barcode) return;
                                // 优先使用 originalCluster（自动重聚类前的原始值）
                                // 如果没有 originalCluster，则使用 originalClusterMap 中保存的值
                                // 最后才使用当前的 cluster（可能已被自动重聚类影响）
                                const originalCluster = row.originalCluster ?? 
                                    originalClusterMap.get(row.barcode) ??
                                    null;
                                manualInitialClusterMap.set(
                                    row.barcode,
                                    originalCluster ??
                                        row.cluster ??
                                        barcodeClusterMap.get(row.barcode) ??
                                        null,
                                );
                            });
                            // Reset manual state when entering manual mode
                            acceptedRows = new Set();
                            manualNewClusters = new Map();
                            // Explicitly trigger cluster history refetch when switching to manual mode
                            // to ensure cluster stability column displays correctly
                            if (rows.length > 0 && baseApi && currentSlice) {
                                fetchClusterHistory();
                            }
                        }}
                        disabled={isSubmitting}
                    >
                        Manual
                    </button>
                </div>
            </div>

            <!-- 第二行：各自按钮 + 批量设置 + 状态 -->
            <div class="flex flex-wrap items-center justify-end gap-2">
                <!-- Batch assign - shown in manual mode -->
                {#if rows.length > 0 && availableClusters.length > 0 && correctionMode === 'manual'}
                    <div class="flex items-center gap-2 px-2.5 py-1.5 bg-slate-50 border border-slate-300 rounded-md">
                        <span class="text-[11px] text-slate-700 whitespace-nowrap">Assign to:</span>
                        <select
                            bind:value={selectedBatchCluster}
                            class="w-40 px-2 py-1.5 text-xs text-slate-800 border border-slate-300 rounded bg-white focus:outline-none focus:ring-1 focus:ring-slate-400"
                        >
                            <option value="">Select</option>
                            {#each availableClusters as cluster}
                                <option value={cluster}>{cluster}</option>
                            {/each}
                        </select>
                        <button
                            type="button"
                            class="h-6 px-2.5 rounded border border-slate-700 bg-slate-700 text-[10px] font-medium text-white hover:bg-slate-800 disabled:opacity-50"
                            on:click={handleBatchAssign}
                            disabled={!selectedBatchCluster || isSubmitting}
                        >
                            Apply
                        </button>
                    </div>
                {/if}

                <div class="flex items-center gap-2">
                    {#if !hasNewCluster && correctionMode === 'auto'}
                        <button
                            type="button"
                            class="h-7 inline-flex items-center gap-1.5 rounded-md border border-slate-700 bg-slate-700 px-2.5 text-xs font-medium text-white shadow-sm transition hover:bg-slate-800 disabled:opacity-50"
                            on:click={handleRecluster}
                            disabled={isSubmitting || rows.length === 0}
                        >
                            {#if isSubmitting}
                                <span class="animate-spin h-3 w-3 rounded-full border-[2px] border-white border-r-transparent"></span>
                            {/if}
                            Recluster
                        </button>
                    {/if}
                    {#if acceptedRows.size > 0}
                        <button
                            type="button"
                            class="h-7 inline-flex items-center gap-1.5 rounded-md border border-slate-600 bg-slate-600 px-2.5 text-xs font-medium text-white shadow-sm transition hover:bg-slate-700 disabled:opacity-50"
                            on:click={handleComplete}
                            disabled={isSubmitting}
                        >
                            Complete ({acceptedRows.size})
                        </button>
                    {/if}
                    <button
                        type="button"
                        class="h-7 inline-flex items-center rounded-md border border-slate-300 bg-white px-2.5 text-xs font-medium text-slate-600 shadow-sm transition hover:bg-slate-100 disabled:opacity-50"
                        on:click={() => {
                            acceptedRows = new Set();
                            dispatch("clear");
                        }}
                        disabled={isSubmitting}
                    >
                        Clear
                    </button>
                </div>

                {#if submitError}
                    <span class="text-[10px] text-red-500 max-w-[220px] truncate text-right" title={submitError}>
                        {submitError}
                    </span>
                {:else if submitSuccess}
                    <!-- <span class="text-[10px] text-green-600 max-w-[220px] truncate text-right" title={submitSuccess}>
                        {submitSuccess}
                    </span> -->
                {/if}
            </div>
        </div>
    </header>

    {#if rows.length === 0}
        <div class="flex-1 flex items-center justify-center border border-dashed border-slate-300 rounded-md text-sm text-slate-500">
            No spots selected. Use the lasso tool on the plot to select spots.
        </div>
    {:else}
        <!-- Content area: statistics on top, table below -->
        <div class="flex-1 min-h-0 flex flex-col gap-3">
            <!-- Statistics Panel - shown above the table whenever there are selected spots -->
            {#if rows.length > 0}
                <div class="flex-none border border-slate-200 rounded-md bg-slate-50/70 max-h-40 overflow-auto">
                    <div class="px-2.5 py-1.5 border-b border-slate-200 bg-slate-50">
                        <div class="flex items-center justify-between gap-2">
                            <div class="flex items-center gap-1.5 text-[11px] font-medium text-slate-700">
                                <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
                                </svg>
                                Statistics
                            </div>
                            <span class="text-[10px] text-slate-600 font-medium whitespace-nowrap">
                                {rows.length} spots
                            </span>
                        </div>
                    </div>
                    
                    <div class="px-2 py-1.5">
                        {#if lassoStatsLoading}
                            <div class="flex items-center gap-1.5 text-[10px] text-slate-500 py-2 justify-center">
                                <span class="animate-spin h-2.5 w-2.5 rounded-full border-[2px] border-slate-300 border-r-transparent"></span>
                                Analyzing...
                            </div>
                        {:else if lassoStatsError}
                            <div class="text-[10px] text-red-500 p-2">{lassoStatsError}</div>
                        {:else if lassoStats}
                            <div class="flex gap-3">
                                <!-- Cluster Distribution (left) -->
                                <div class="flex-1 min-w-0">
                                    <div class="text-[10px] font-medium text-slate-500 mb-0.5">
                                        Distribution
                                    </div>
                                    <div class="space-y-0.5">
                                        {#each lassoStats.cluster_distribution.slice(0, 5) as dist}
                                            <div class="flex items-center gap-1 text-[10px] leading-snug">
                                                <span 
                                                    class="w-2 h-2 rounded-full flex-shrink-0"
                                                    style="background-color: {getClusterColor(dist.cluster)};"
                                                ></span>
                                                <span
                                                    style="color: {getClusterColor(dist.cluster)};"
                                                    class="font-medium truncate max-w-[60px]"
                                                    title={dist.cluster}
                                                >
                                                    {dist.cluster}
                                                </span>
                                                <span class="text-slate-400 ml-auto whitespace-nowrap">
                                                    {dist.count}
                                                    <span class="text-slate-300">({dist.percentage}%)</span>
                                                </span>
                                            </div>
                                        {/each}
                                    </div>
                                </div>
                                
                                <!-- Top Genes (right) -->
                                <div class="flex-1 min-w-0">
                                    <div class="text-[10px] font-medium text-slate-500 mb-0.5">
                                        Top Genes
                                    </div>
                                    <div class="flex flex-wrap gap-0.5">
                                        {#each lassoStats.top_expressed_genes.slice(0, 8) as gene}
                                            <span
                                                class="px-1 py-0.5 rounded bg-slate-100 text-[9px] text-slate-600 truncate max-w-[72px]"
                                                title={gene.gene}
                                            >
                                                {gene.gene}
                                            </span>
                                        {/each}
                                    </div>
                                </div>
                            </div>
                        {:else}
                            <div class="text-[10px] text-slate-400 p-2">No data</div>
                        {/if}
                    </div>
                </div>
            {/if}
            
            <!-- Table -->
            <div class="flex-1 min-h-0 min-w-0 overflow-auto border border-slate-200 rounded-md" bind:this={tableContainer}>
            <table class="table caption-bottom text-xs w-full" style="table-layout: auto; max-width: 100%;">
                <thead class="bg-slate-50">
                    <tr>
                        <th class="px-3 py-2 text-left w-14">#</th>
                        <th class="px-3 py-2 text-left">Barcode</th>
                        {#if hasCurrentCluster}
                            <th class="px-3 py-2 text-left">Current Cluster</th>
                            <th class="px-3 py-2 text-left">
                                <div class="inline-flex items-center gap-1 relative">
                                    <span>Cluster stability</span>
                                    <span
                                        class="inline-flex items-center justify-center text-slate-400 hover:text-slate-600 cursor-help relative"
                                        aria-label="Cluster stability help"
                                        role="button"
                                        tabindex="0"
                                        on:mouseenter={() => (showHistoryHint = true)}
                                        on:mouseleave={() => (showHistoryHint = false)}
                                    >
                                        <Info size={12} />

                                        {#if showHistoryHint}
                                            <div
                                                class="absolute z-30 mt-20 right-5 w-64 max-w-[70vw] text-[11px] leading-snug text-slate-50 bg-slate-900/95 rounded-md shadow-lg px-2.5 py-1.5"
                                            >
                                                For each spot, this column summarizes how often it is assigned
                                                to different clusters in other results. The numbers after each
                                                cluster label are counts of results (e.g. <span class="font-mono">2:5</span>
                                                means this spot was in cluster 2 in 5 other results).
                                            </div>
                                        {/if}
                                    </span>
                                </div>
                            </th>
                        {/if}
                        <!-- Original column is kept for internal logic but hidden in UI -->
                        {#if hasNewCluster || correctionMode === 'manual'}
                            <th class="px-3 py-2 text-left">New</th>
                        {/if}
                        {#if hasComment}
                            <th class="px-3 py-2 text-left">Comment</th>
                        {/if}
                        {#if hasUpdatedAt}
                            <th class="px-3 py-2 text-left whitespace-nowrap">Updated</th>
                        {/if}
                        {#if hasNewCluster && correctionMode === 'auto'}
                            <th class="px-3 py-2 text-left w-24">Action</th>
                        {/if}
                    </tr>
                </thead>
                <tbody
                    class="[&>tr]:hover:bg-slate-100"
                    on:mouseleave={resetHover}
                >
                    {#each rows as row (row.uid)}
                        <tr
                            data-row-uid={row.uid}
                            class="cursor-pointer transition-colors {row.hasChanged ? 'bg-red-50' : ''}"
                            style={highlightedRowUid === row.uid ? 'background-color: rgb(219 234 254) !important; box-shadow: 0 0 0 2px rgb(96 165 250);' : ''}
                            on:mouseenter={() => handleHover(row)}
                            on:click={() => toggleRow(row)}
                        >
                            <td class="px-3 py-2 text-slate-500">{row.index}</td>
                            <td class="px-3 py-2 font-medium text-slate-700">{row.barcode}</td>
                            {#if hasCurrentCluster}
                                <td
                                    class="px-3 py-2"
                                    style={`color:${getClusterColor(row.cluster)};`}
                                >
                                    {row.cluster ?? "-"}
                                </td>
                                <td class="px-3 py-2">
                                    {#if clusterHistoryLoading}
                                        <span class="text-[10px] text-slate-400">...</span>
                                    {:else}
                                        {@const history = summarizeClusterHistory(row.barcode)}
                                        {#if history && history.length > 0}
                                            <div class="text-[10px] text-slate-600">
                                                {#each history.slice(0, 3) as h, idx}
                                                    <span style="color: {getClusterColor(h.cluster)};" class="font-medium">{h.cluster}</span><span class="text-slate-400">:{h.count}</span>{#if idx < Math.min(history.length, 3) - 1}<span class="text-slate-300 mx-0.5">/</span>{/if}
                                                {/each}
                                            </div>
                                        {:else}
                                            <span class="text-[10px] text-slate-300">-</span>
                                        {/if}
                                    {/if}
                                </td>
                            {/if}
                            <!-- New column -->
                            {#if hasNewCluster || correctionMode === 'manual'}
                                {#if correctionMode === 'manual'}
                                    {@const currentOriginal =
                                        row.originalCluster ?? row.cluster ?? ''}
                                    {@const currentValue =
                                        manualNewClusters.get(row.barcode) ??
                                        row.newCluster ??
                                        currentOriginal}
                                    <td
                                        class="px-3 py-2"
                                        style={`color:${getClusterColor(currentValue)};`}
                                    >
                                        <div class="flex items-center gap-1.5">
                                            <select
                                                class="px-2 py-1 text-xs border border-slate-300 rounded bg-white text-slate-800 focus:outline-none focus:ring-1 focus:ring-slate-400 w-24 min-w-[5.5rem]"
                                                value={currentValue}
                                                on:change={(e) =>
                                                    handleManualSetCluster(
                                                        row,
                                                        e.currentTarget.value,
                                                    )}
                                            >
                                                <option value={currentOriginal}>
                                                    {currentOriginal || '-'}
                                                </option>
                                                {#each availableClusters as cluster}
                                                    {#if cluster !== currentOriginal}
                                                        <option value={cluster}>
                                                            {cluster}
                                                        </option>
                                                    {/if}
                                                {/each}
                                            </select>
                                        </div>
                                    </td>
                                {:else}
                                    <td
                                        class="px-3 py-2"
                                        style={`color:${getClusterColor(row.newCluster)};`}
                                    >
                                        {row.newCluster ?? "-"}
                                    </td>
                                {/if}
                            {/if}
                            {#if hasComment}
                                <td class="px-3 py-2 text-slate-500">
                                    {row.comment ?? "-"}
                                </td>
                            {/if}
                            {#if hasUpdatedAt}
                                <td class="px-3 py-2 text-slate-500 whitespace-nowrap">
                                    {row.updatedAt ?? "-"}
                                </td>
                            {/if}
                            {#if hasNewCluster && correctionMode === 'auto'}
                                <td class="px-3 py-2">
                                    {#if row.newCluster && row.newCluster !== row.originalCluster}
                                        {#if acceptedRows.has(row.uid)}
                                            <button
                                                type="button"
                                                class="inline-flex items-center gap-1 rounded-md border border-slate-400 bg-slate-400 px-2 py-1 text-[11px] font-medium text-white shadow-sm transition hover:bg-slate-500"
                                                on:click|stopPropagation={(e) => {
                                                    e.stopPropagation();
                                                    handleAcceptRow(row);
                                                }}
                                                title="Cancel and restore to original cluster"
                                            >
                                                Cancel
                                            </button>
                                        {:else}
                                            <button
                                                type="button"
                                                class="inline-flex items-center gap-1 rounded-md border border-slate-700 bg-slate-700 px-2 py-1 text-[11px] font-medium text-white shadow-sm transition hover:bg-slate-800"
                                                on:click|stopPropagation={(e) => {
                                                    e.stopPropagation();
                                                    handleAcceptRow(row);
                                                }}
                                                title="Accept this change"
                                            >
                                                Accept
                                            </button>
                                        {/if}
                                    {:else}
                                        <span class="text-[11px] text-slate-400">—</span>
                                    {/if}
                                </td>
                            {/if}
                        </tr>
                        {#if expandedUid === row.uid}
                            <tr class="bg-slate-50">
                                <td colspan={
                                    2 +
                                    (!hasNewCluster && hasCurrentCluster ? 2 : 0) +
                                    (hasOriginalCluster ? 1 : 0) +
                                    (hasNewCluster ? 1 : 0) +
                                    (hasComment ? 1 : 0) +
                                    (hasUpdatedAt ? 1 : 0) +
                                    (hasNewCluster ? 1 : 0)
                                } class="px-4 py-3">
                                    <!-- Gene Expression (always shown) -->
                                    {#each [geneExpressionData.get(row.barcode)] as geneData}
                                        <div class="{correctionMode === 'auto' && hasDetails && row.details && Object.keys(row.details).length > 0 ? 'mb-3' : ''}">
                                            <div class="text-[11px] font-medium text-slate-600 mb-2 flex items-center gap-2">
                                                <svg class="w-3.5 h-3.5 text-blue-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
                                                </svg>
                                                Top Gene Expression
                                            </div>
                                            {#if geneData?.loading}
                                                <div class="flex items-center gap-2 text-[11px] text-slate-500">
                                                    <span class="animate-spin h-3 w-3 rounded-full border-[2px] border-slate-300 border-r-transparent"></span>
                                                    Loading gene expression...
                                                </div>
                                            {:else if geneData?.error}
                                                <div class="text-[11px] text-red-500">
                                                    {geneData.error}
                                                </div>
                                            {:else if geneData?.data?.top_genes}
                                                <div class="grid grid-cols-3 gap-x-4 gap-y-1">
                                                    {#each geneData.data.top_genes as gene, idx}
                                                        <div class="flex items-center justify-between gap-2 text-[10px]">
                                                            <span class="font-medium text-slate-700 truncate" title={gene.gene}>
                                                                {gene.gene}
                                                            </span>
                                                            <span class="font-mono text-slate-500 whitespace-nowrap">
                                                                {gene.expression.toFixed(2)}
                                                            </span>
                                                        </div>
                                                    {/each}
                                                </div>
                                                {#if geneData.data.total_expression}
                                                    <div class="mt-2 text-[10px] text-slate-400">
                                                        Total expression: {geneData.data.total_expression.toFixed(2)} | 
                                                        Genes detected: {geneData.data.total_genes}
                                                    </div>
                                                {/if}
                                            {:else}
                                                <div class="text-[11px] text-slate-400">
                                                    No gene expression data available
                                                </div>
                                            {/if}
                                        </div>
                                    {/each}
                                    
                                    <!-- Other details (only in auto mode) -->
                                    {#if correctionMode === 'auto' && hasDetails && row.details && Object.keys(row.details).length > 0}
                                        <div class="grid gap-2 text-[11px] text-slate-600 border-t border-slate-200 pt-3">
                                            {#each Object.entries(row.details) as [key, value]}
                                                <div class="flex items-center gap-2">
                                                    <span class="uppercase tracking-wide text-slate-400">
                                                        {key}
                                                    </span>
                                                    <span class="font-mono text-slate-600 break-all">
                                                        {value ?? "-"}
                                                    </span>
                                                </div>
                                            {/each}
                                        </div>
                                    {/if}
                                </td>
                            </tr>
                        {/if}
                    {/each}
                </tbody>
                <tfoot class="bg-slate-50">
                    <tr>
                        <td class="px-3 py-2 text-slate-500" colspan="99">
                            {#if !hasNewCluster}
                                Click a row to view gene expression data.
                            {:else}
                                Click a row to reveal additional details.
                            {/if}
                        </td>
                    </tr>
                </tfoot>
            </table>
            </div>
        </div>
    {/if}
</div>
