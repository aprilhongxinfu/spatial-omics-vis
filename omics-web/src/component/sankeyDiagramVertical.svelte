<script>
    import { onDestroy, onMount } from "svelte";
    import { createEventDispatcher } from "svelte";

    // Props
    export let visibleDistributionSummary = [];
    export let selectedResultId = null;
    export let verticalLayout = false;
    export let scrollSource = null;
    export let maxStabilityPercent = 100;
    export let flowMode = "individual";
    export let segmentMode = "next_cluster";
    export let highlightedBarcodeFromPreview = null;
    // barcodeClusterDistribution and barcodeStabilityPercent need to be passed as props
    // since they are computed in the parent component
    export let barcodeClusterDistribution = new Map();
    export let barcodeStabilityPercent = new Map();
    // Cluster-level metrics: cluster_result_id -> { cluster -> metrics }
    // (Currently disabled - metric lines removed)
    // export let clusterMetricsData = new Map();

    const dispatch = createEventDispatcher();

    // Internal state
    let connectionWrapper;
    let renderCanvas;
    let layoutCache = [];
    let connectionCache = [];
    let canvasMetrics = { width: 0, height: 0 };
    let hoverInfo = null;
    let rowVisibleCache = [];
    let tooltipPosition = { left: 0, top: 0 };
    let highlightedBarcodeFromSankey = null;
    export let focusedClusterByRow = {};
    export let activeFocus = null;
    export let activeFocusRank = null;

    // Export cluster layout info for alignment with other components
    // Map<rowIndex, Map<clusterName, { x, width }>>
    export let clusterLayoutInfo = new Map();
    // Export sankey dimensions for alignment
    // After 90° rotation: original y becomes screen x
    // rowPositions: array of screen x positions for each result row
    export let sankeyDimensions = {
        paddingLeft: 24,
        sankeyWidth: 0,
        totalWidth: 0,
        rowPositions: [],
        rowHeight: 0,
    };
    let measureRaf = 0;
    let measureQueued = false;
    let resizeObserver = null;
    let activeScrollSource = null;
    let detachScrollSync = () => {};
    let clickTimer = null; // For debouncing click vs double-click

    // Helper constants
    const ADJ_STABLE_KEY = "__adj_stable__";

    // Helper functions
    function parseClusterNumber(value) {
        const s =
            value === null || value === undefined ? "" : String(value).trim();
        if (!s) return null;
        const n = Number.parseFloat(s);
        return Number.isNaN(n) ? null : n;
    }

    function compareClusterLabel(a, b) {
        const sa = a === null || a === undefined ? "" : String(a).trim();
        const sb = b === null || b === undefined ? "" : String(b).trim();
        const na = parseClusterNumber(sa);
        const nb = parseClusterNumber(sb);
        const bothNumeric = na !== null && nb !== null;
        return bothNumeric
            ? na - nb
            : sa.localeCompare(sb, undefined, { numeric: true });
    }

    function compareClusterDistanceFrom(fromCluster, toA, toB) {
        const fromStr =
            fromCluster === null || fromCluster === undefined
                ? ""
                : String(fromCluster).trim();
        const aStr =
            toA === null || toA === undefined ? "" : String(toA).trim();
        const bStr =
            toB === null || toB === undefined ? "" : String(toB).trim();
        const fromNum = parseClusterNumber(fromStr);
        const aNum = parseClusterNumber(aStr);
        const bNum = parseClusterNumber(bStr);
        if (fromNum !== null && aNum !== null && bNum !== null) {
            const da = aNum - fromNum;
            const db = bNum - fromNum;
            const absCmp = Math.abs(da) - Math.abs(db);
            if (absCmp !== 0) return absCmp;
            const dirRank = (d) => (d < 0 ? 0 : d === 0 ? 1 : 2);
            const dirCmp = dirRank(da) - dirRank(db);
            if (dirCmp !== 0) return dirCmp;
            return da - db;
        }
        return compareClusterLabel(aStr, bStr);
    }

    function getNextClusterKey(rowVisible, rowIndex, barcode) {
        const bc =
            barcode === null || barcode === undefined
                ? ""
                : `${barcode}`.trim();
        if (!bc) return null;
        if (rowIndex >= rowVisible.length - 1) return "End";
        const nextMap = rowVisible[rowIndex + 1]?.barcodeClusterMap;
        const nextC = nextMap ? nextMap.get(bc) : null;
        return nextC === null || nextC === undefined || `${nextC}`.trim() === ""
            ? "Gone"
            : `${nextC}`.trim();
    }

    function getPrevClusterKey(rowVisible, rowIndex, barcode) {
        const bc =
            barcode === null || barcode === undefined
                ? ""
                : `${barcode}`.trim();
        if (!bc) return null;
        if (rowIndex <= 0) return "Start";
        const prevMap = rowVisible[rowIndex - 1]?.barcodeClusterMap;
        const prevC = prevMap ? prevMap.get(bc) : null;
        return prevC === null || prevC === undefined || `${prevC}`.trim() === ""
            ? "Gone"
            : `${prevC}`.trim();
    }

    function orderByNextClusterDistance(keys, currentCluster) {
        const curr = String(currentCluster ?? "").trim();
        const currNum = parseClusterNumber(curr);

        const center = [];
        const left = [];
        const right = [];
        const other = [];

        for (const k of keys) {
            const kk = String(k ?? "").trim();
            if (!kk) continue;
            if (kk === curr) {
                center.push(kk);
                continue;
            }
            if (kk === "End" || kk === "Gone") {
                other.push(kk);
                continue;
            }
            const kn = parseClusterNumber(kk);
            if (currNum !== null && kn !== null) {
                if (kn < currNum)
                    left.push({
                        key: kk,
                        dist: Math.abs(currNum - kn),
                        num: kn,
                    });
                else
                    right.push({
                        key: kk,
                        dist: Math.abs(currNum - kn),
                        num: kn,
                    });
            } else {
                const cmp = compareClusterLabel(kk, curr);
                if (cmp < 0) left.push({ key: kk, dist: 1, num: null });
                else right.push({ key: kk, dist: 1, num: null });
            }
        }

        left.sort((a, b) => {
            if (a.dist !== b.dist) return b.dist - a.dist;
            return compareClusterLabel(a.key, b.key);
        });
        right.sort((a, b) => {
            if (a.dist !== b.dist) return a.dist - b.dist;
            return compareClusterLabel(a.key, b.key);
        });
        other.sort((a, b) => a.localeCompare(b));

        return [
            ...left.map((x) => x.key),
            ...center,
            ...right.map((x) => x.key),
            ...other,
        ];
    }

    // Transform screen coordinates to logical drawing coordinates
    // Since we rotate 90° clockwise with translate(drawHeight, 0) + rotate(PI/2):
    // Drawing (x, y) -> Screen (drawHeight - y, x)
    // Inverse: Screen (sx, sy) -> Drawing (sy, drawHeight - sx)
    function screenToLogical(screenX, screenY) {
        const { height: drawHeight } = canvasMetrics;
        return {
            x: screenY,
            y: drawHeight - screenX,
        };
    }

    function isAdjacentStableAtRow(rowVisible, rowIndex, barcode, clusterName) {
        const bc =
            barcode === null || barcode === undefined
                ? ""
                : `${barcode}`.trim();
        if (!bc) return false;
        const cName =
            clusterName === null || clusterName === undefined
                ? ""
                : `${clusterName}`.trim();
        if (!cName) return false;

        const hasPrev = rowIndex > 0;
        const hasNext = rowIndex < rowVisible.length - 1;
        if (!hasPrev && !hasNext) return false;

        const prevMap = hasPrev
            ? rowVisible[rowIndex - 1].barcodeClusterMap
            : null;
        const nextMap = hasNext
            ? rowVisible[rowIndex + 1].barcodeClusterMap
            : null;

        if (hasPrev) {
            const prevC = prevMap ? prevMap.get(bc) : null;
            if (`${prevC ?? ""}`.trim() !== cName) return false;
        }
        if (hasNext) {
            const nextC = nextMap ? nextMap.get(bc) : null;
            if (`${nextC ?? ""}`.trim() !== cName) return false;
        }
        return true;
    }

    function calculateTrendSimilarity(seq1, seq2) {
        if (!seq1 || !seq2 || seq1.length !== seq2.length) {
            return 0;
        }

        let matchCount = 0;
        let validCount = 0;
        let numericSimilaritySum = 0;
        let numericCount = 0;

        for (let i = 0; i < seq1.length; i++) {
            const c1 = seq1[i];
            const c2 = seq2[i];

            if (c1 === null || c2 === null) {
                if (c1 === c2) {
                    matchCount++;
                }
                continue;
            }

            validCount++;

            if (c1 === c2) {
                matchCount++;
                continue;
            }

            const num1 = Number.parseFloat(c1);
            const num2 = Number.parseFloat(c2);

            if (!Number.isNaN(num1) && !Number.isNaN(num2)) {
                const allNums = [];
                seq1.forEach((c) => {
                    if (c !== null) {
                        const n = Number.parseFloat(c);
                        if (!Number.isNaN(n)) allNums.push(n);
                    }
                });
                seq2.forEach((c) => {
                    if (c !== null) {
                        const n = Number.parseFloat(c);
                        if (!Number.isNaN(n)) allNums.push(n);
                    }
                });

                if (allNums.length > 0) {
                    const min = Math.min(...allNums);
                    const max = Math.max(...allNums);
                    const range = max - min;

                    if (range > 0) {
                        const normalized1 = (num1 - min) / range;
                        const normalized2 = (num2 - min) / range;
                        const diff = Math.abs(normalized1 - normalized2);
                        const similarity = 1 - Math.min(diff, 1);
                        numericSimilaritySum += similarity;
                        numericCount++;
                    }
                }
            }
        }

        const exactMatchScore = validCount > 0 ? matchCount / validCount : 0;
        const numericSimilarityScore =
            numericCount > 0 ? numericSimilaritySum / numericCount : 0;

        return exactMatchScore * 0.7 + numericSimilarityScore * 0.3;
    }

    function isTrendStable(sequence) {
        if (!sequence || sequence.length === 0) return false;
        const first = sequence[0];
        if (first === null) return false;
        return sequence.every((cluster) => cluster === first);
    }

    function sortTrendsBySimilarity(trendGroups) {
        const trendEntries = Array.from(trendGroups.entries()).map(
            ([key, group]) => ({
                key,
                sequence: group.sequence,
                group,
                isStable: isTrendStable(group.sequence),
            }),
        );

        if (trendEntries.length <= 1) {
            return trendEntries.map((e) => e.key);
        }

        const stableTrends = trendEntries.filter((e) => e.isStable);
        const nonStableTrends = trendEntries.filter((e) => !e.isStable);

        stableTrends.sort((a, b) => {
            const aVal = a.sequence[0] || "";
            const bVal = b.sequence[0] || "";
            const numA = Number.parseFloat(aVal);
            const numB = Number.parseFloat(bVal);
            if (!Number.isNaN(numA) && !Number.isNaN(numB)) {
                return numA - numB;
            }
            return String(aVal).localeCompare(String(bVal));
        });

        if (nonStableTrends.length <= 1) {
            const ordered = stableTrends.concat(nonStableTrends);
            return ordered.map((e) => e.key);
        }

        const similarities = [];
        for (let i = 0; i < nonStableTrends.length; i++) {
            similarities[i] = [];
            for (let j = 0; j < nonStableTrends.length; j++) {
                if (i === j) {
                    similarities[i][j] = 1.0;
                } else if (j < i) {
                    similarities[i][j] = similarities[j][i];
                } else {
                    similarities[i][j] = calculateTrendSimilarity(
                        nonStableTrends[i].sequence,
                        nonStableTrends[j].sequence,
                    );
                }
            }
        }

        const orderedNonStable = [0];
        const remaining = Array.from(
            { length: nonStableTrends.length - 1 },
            (_, i) => i + 1,
        );

        while (remaining.length > 0) {
            const lastIdx = orderedNonStable[orderedNonStable.length - 1];
            let bestNextIdx = remaining[0];
            let bestSimilarity = similarities[lastIdx][bestNextIdx];

            for (const candidateIdx of remaining) {
                const sim = similarities[lastIdx][candidateIdx];
                if (sim > bestSimilarity) {
                    bestSimilarity = sim;
                    bestNextIdx = candidateIdx;
                }
            }

            orderedNonStable.push(bestNextIdx);
            const removeIdx = remaining.indexOf(bestNextIdx);
            remaining.splice(removeIdx, 1);
        }

        const orderedNonStableEntries = orderedNonStable.map(
            (idx) => nonStableTrends[idx],
        );
        const finalOrdered = stableTrends.concat(orderedNonStableEntries);

        return finalOrdered.map((e) => e.key);
    }

    function jaccardSimilarity(setA, setB) {
        const a = new Set(Array.isArray(setA) ? setA : []);
        const b = new Set(Array.isArray(setB) ? setB : []);
        if (a.size === 0 && b.size === 0) return 1;
        let inter = 0;
        for (const v of a) if (b.has(v)) inter += 1;
        const union = a.size + b.size - inter;
        return union ? inter / union : 0;
    }

    function sortClusterSetsBySimilarity(trendGroups) {
        const entries = Array.from(trendGroups.entries()).map(
            ([key, group]) => ({
                key,
                set: group.sequence || [],
            }),
        );
        if (entries.length <= 1) return entries.map((e) => e.key);

        entries.sort((a, b) => {
            const sa = a.set?.length ?? 0;
            const sb = b.set?.length ?? 0;
            if (sa !== sb) return sa - sb;
            return String(a.key).localeCompare(String(b.key));
        });

        const ordered = [entries[0]];
        const remaining = entries.slice(1);

        while (remaining.length) {
            const last = ordered[ordered.length - 1];
            let bestIdx = 0;
            let bestSim = -1;
            for (let i = 0; i < remaining.length; i++) {
                const sim = jaccardSimilarity(last.set, remaining[i].set);
                if (sim > bestSim) {
                    bestSim = sim;
                    bestIdx = i;
                }
            }
            ordered.push(remaining.splice(bestIdx, 1)[0]);
        }

        return ordered.map((e) => e.key);
    }

    function isBarcodeStable(barcode) {
        if (!barcodeClusterDistribution.has(barcode)) {
            return false;
        }
        const clusterMap = barcodeClusterDistribution.get(barcode);
        if (clusterMap.size === 1) {
            const totalResults = visibleDistributionSummary.length;
            const clusterEntry = Array.from(clusterMap.values())[0];
            return clusterEntry.count === totalResults;
        }
        return false;
    }

    function getBarcodeStabilityPercent(barcode) {
        const bc =
            barcode === null || barcode === undefined
                ? ""
                : `${barcode}`.trim();
        if (!bc) return 0;
        return barcodeStabilityPercent.get(bc) ?? 0;
    }

    function isBarcodeFilteredOutByStability(barcode) {
        return getBarcodeStabilityPercent(barcode) > maxStabilityPercent;
    }

    const getResultLabel = (summary) => {
        const { result } = summary ?? {};
        return (
            result?.result_name ||
            result?.cluster_result_id ||
            result?.result_id ||
            "Unnamed result"
        );
    };

    // ============================================================================
    // Barycenter-based ordering module
    // ============================================================================

    /**
     * Compute row orders using bidirectional barycenter algorithm
     * @param {Array} rowVisibleCache - Array of row visible data
     * @param {Object} options - Configuration options
     * @returns {Object} { rowOrders, posMaps }
     */
    function computeRowOrders(rowVisibleCache, options = {}) {
        const {
            segmentMode = "cluster_only",
            flowMode = "individual",
            nIters = 1,
            alpha = 0.7,
            beta = 0.15,
            gamma = 0.2,
            globalBarcodeRank = null,
        } = options;

        // console.log("computeRowOrders nIters=", nIters);

        const nRows = rowVisibleCache.length;
        if (nRows === 0) {
            return { rowOrders: [], posMaps: [] };
        }

        // Step A: Initialize posMaps
        const posMaps = [];
        for (let i = 0; i < nRows; i++) {
            posMaps.push(new Map());
        }

        // Initialize row 0 with globalBarcodeRank or default order
        if (globalBarcodeRank && globalBarcodeRank.size > 0) {
            const row0Spots = rowVisibleCache[0]?.visibleSpots || [];
            let rank = 0;
            for (const spot of row0Spots) {
                const bc = `${spot?.barcode ?? ""}`.trim();
                if (bc && globalBarcodeRank.has(bc)) {
                    posMaps[0].set(bc, globalBarcodeRank.get(bc));
                } else if (bc) {
                    posMaps[0].set(bc, rank++);
                }
            }
        } else {
            // Default: use existing order from visibleSpots
            const row0Spots = rowVisibleCache[0]?.visibleSpots || [];
            row0Spots.forEach((spot, idx) => {
                const bc = `${spot?.barcode ?? ""}`.trim();
                if (bc) posMaps[0].set(bc, idx);
            });
        }

        // Initialize other rows with simple order
        for (let i = 1; i < nRows; i++) {
            const rowSpots = rowVisibleCache[i]?.visibleSpots || [];
            rowSpots.forEach((spot, idx) => {
                const bc = `${spot?.barcode ?? ""}`.trim();
                if (bc) posMaps[i].set(bc, idx);
            });
        }

        // Helper: Get trend key for a spot
        function getTrendKey(spot, barcode, rowIdx) {
            if (segmentMode === "cluster_only") {
                return "__cluster_only__";
            } else if (segmentMode === "next_cluster") {
                if (rowIdx === nRows - 1) {
                    // Last row: use previous cluster
                    if (rowIdx <= 0) return "Start";
                    const prevMap =
                        rowVisibleCache[rowIdx - 1]?.barcodeClusterMap;
                    if (!prevMap) return "Gone";
                    const prevC = prevMap.get(barcode);
                    return prevC === null ||
                        prevC === undefined ||
                        `${prevC}`.trim() === ""
                        ? "Gone"
                        : `${prevC}`.trim();
                } else {
                    // Not last row: use next cluster
                    if (rowIdx >= nRows - 1) return "End";
                    const nextMap =
                        rowVisibleCache[rowIdx + 1]?.barcodeClusterMap;
                    if (!nextMap) return "Gone";
                    const nextC = nextMap.get(barcode);
                    return nextC === null ||
                        nextC === undefined ||
                        `${nextC}`.trim() === ""
                        ? "Gone"
                        : `${nextC}`.trim();
                }
            } else if (segmentMode === "cluster_set") {
                return spot?.clusterSetKey || "";
            } else {
                return spot?.trendKey || "";
            }
        }

        // Helper: Build sequence for a group based on segmentMode
        function buildSequence(spot, bc, rowIdx, clusterName) {
            if (segmentMode === "cluster_set") {
                return spot?.clusterSet || [];
            } else if (segmentMode === "trend_sequence") {
                return spot?.trendSequence || [];
            } else if (segmentMode === "next_cluster") {
                if (rowIdx === nRows - 1) {
                    const prevMap =
                        rowVisibleCache[rowIdx - 1]?.barcodeClusterMap;
                    const prevC = prevMap?.get(bc);
                    return [prevC ? `${prevC}`.trim() : "?", clusterName];
                } else {
                    const nextMap =
                        rowVisibleCache[rowIdx + 1]?.barcodeClusterMap;
                    const nextC = nextMap?.get(bc);
                    return [clusterName, nextC ? `${nextC}`.trim() : "?"];
                }
            }
            return [];
        }

        // Helper: Get group signature for similarity
        function getGroupSignature(group) {
            if (group.isAdjStableGroup) {
                return ADJ_STABLE_KEY;
            }
            if (segmentMode === "cluster_set") {
                return JSON.stringify(group.sequence || []);
            }
            return JSON.stringify(group.sequence || []);
        }

        // Helper: Calculate barycenter score
        function calculateBarycenterScore(element, rowIdx, posMaps, direction) {
            const bc = `${element?.barcode ?? ""}`.trim();
            if (!bc) return 0;

            const stability = getBarcodeStabilityPercent(bc);
            const weight = 1 + 0.8 * (1 - stability / 100);

            let barySum = 0;
            let totalWeight = 0;

            if (direction === "forward" && rowIdx < nRows - 1) {
                const nextMap = rowVisibleCache[rowIdx + 1]?.barcodeClusterMap;
                if (nextMap) {
                    const nextCluster = nextMap.get(bc);
                    if (nextCluster) {
                        const nextPos = posMaps[rowIdx + 1].get(bc);
                        if (nextPos !== undefined) {
                            barySum += nextPos * weight;
                            totalWeight += weight;
                        }
                    }
                }
            } else if (direction === "backward" && rowIdx > 0) {
                const prevMap = rowVisibleCache[rowIdx - 1]?.barcodeClusterMap;
                if (prevMap) {
                    const prevCluster = prevMap.get(bc);
                    if (prevCluster) {
                        const prevPos = posMaps[rowIdx - 1].get(bc);
                        if (prevPos !== undefined) {
                            barySum += prevPos * weight;
                            totalWeight += weight;
                        }
                    }
                }
            }

            return totalWeight > 0 ? barySum / totalWeight : 0;
        }

        // Helper: Calculate trend similarity penalty (as tie-breaker)
        function calculateTrendSimilarityPenalty(group, otherGroups) {
            if (otherGroups.length === 0) return 0;
            const groupSig = getGroupSignature(group);
            let maxSim = 0;
            for (const other of otherGroups) {
                const otherSig = getGroupSignature(other);
                if (groupSig === otherSig) {
                    maxSim = 1;
                    break;
                }
                // Simple similarity: if sequences share elements
                if (group.sequence && other.sequence) {
                    const setA = new Set(group.sequence);
                    const setB = new Set(other.sequence);
                    let inter = 0;
                    for (const v of setA) if (setB.has(v)) inter++;
                    const union = setA.size + setB.size - inter;
                    const sim = union > 0 ? inter / union : 0;
                    if (sim > maxSim) maxSim = sim;
                }
            }
            return 1 - maxSim; // Penalty: lower similarity = higher penalty
        }

        // Helper: Calculate adjacent stable center penalty
        function calculateAdjacentStablePenalty(
            spot,
            clusterName,
            rowIdx,
            clusterSize,
        ) {
            const bc = `${spot?.barcode ?? ""}`.trim();
            if (!bc) return 0;
            if (
                isAdjacentStableAtRow(rowVisibleCache, rowIdx, bc, clusterName)
            ) {
                // Small penalty to pull towards center (position clusterSize/2)
                const centerPos = clusterSize / 2;
                return (
                    Math.abs(centerPos - (posMaps[rowIdx].get(bc) || 0)) * 0.1
                );
            }
            return 0;
        }

        // Step B: Iterative update (Forward + Backward)
        for (let iter = 0; iter < nIters; iter++) {
            // Forward pass
            for (let i = 0; i < nRows - 1; i++) {
                const rowData = rowVisibleCache[i];
                const clusters = new Map();

                // Group spots by cluster
                (rowData?.visibleSpots || []).forEach((spot) => {
                    const cluster = String(spot?.cluster ?? "").trim();
                    if (!cluster) return;
                    if (!clusters.has(cluster)) {
                        clusters.set(cluster, []);
                    }
                    clusters.get(cluster).push(spot);
                });

                // Process each cluster
                for (const [clusterName, clusterSpots] of clusters.entries()) {
                    // Build groups
                    const groups = new Map();
                    if (segmentMode !== "cluster_only") {
                        // Group by trend key
                        clusterSpots.forEach((spot) => {
                            const bc = `${spot?.barcode ?? ""}`.trim();
                            if (!bc) return;
                            const trendKey = getTrendKey(spot, bc, i);
                            const key = trendKey || "__default__";
                            if (!groups.has(key)) {
                                groups.set(key, {
                                    key,
                                    sequence: buildSequence(
                                        spot,
                                        bc,
                                        i,
                                        clusterName,
                                    ),
                                    spots: [],
                                    isAdjStableGroup: key === ADJ_STABLE_KEY,
                                });
                            }
                            groups.get(key).spots.push(spot);
                        });
                    } else {
                        // Single group for cluster_only
                        groups.set("__cluster_only__", {
                            key: "__cluster_only__",
                            sequence: [clusterName],
                            spots: clusterSpots,
                            isAdjStableGroup: false,
                        });
                    }

                    // Sort groups by barycenter score
                    const groupArray = Array.from(groups.entries()).map(
                        ([key, group]) => ({
                            key,
                            group,
                        }),
                    );

                    groupArray.forEach(({ group }) => {
                        // Calculate group score (average of spot scores)
                        let groupScore = 0;
                        let count = 0;
                        group.spots.forEach((spot) => {
                            const baryNext = calculateBarycenterScore(
                                spot,
                                i,
                                posMaps,
                                "forward",
                            );
                            groupScore += baryNext;
                            count++;
                        });
                        group._score = count > 0 ? groupScore / count : 0;
                    });

                    // Sort groups
                    groupArray.sort((a, b) => {
                        // Stable groups first (if all spots are stable)
                        const aAllStable = a.group.spots.every((s) => {
                            const bc = `${s?.barcode ?? ""}`.trim();
                            return bc && isBarcodeStable(bc);
                        });
                        const bAllStable = b.group.spots.every((s) => {
                            const bc = `${s?.barcode ?? ""}`.trim();
                            return bc && isBarcodeStable(bc);
                        });
                        if (aAllStable !== bAllStable) {
                            return aAllStable ? -1 : 1;
                        }

                        // Then by barycenter score
                        if (Math.abs(a.group._score - b.group._score) > 0.01) {
                            return a.group._score - b.group._score;
                        }

                        // Tie-breaker: trend similarity
                        const otherGroups = groupArray
                            .filter((g) => g.key !== a.key && g.key !== b.key)
                            .map((g) => g.group);
                        const aPenalty = calculateTrendSimilarityPenalty(
                            a.group,
                            otherGroups,
                        );
                        const bPenalty = calculateTrendSimilarityPenalty(
                            b.group,
                            otherGroups,
                        );
                        if (Math.abs(aPenalty - bPenalty) > 0.001) {
                            return aPenalty - bPenalty;
                        }

                        return String(a.key).localeCompare(String(b.key));
                    });

                    // Sort spots within each group
                    const orderedBarcodes = [];
                    groupArray.forEach(({ group }) => {
                        // Separate stable and non-stable spots
                        const stableSpots = [];
                        const nonStableSpots = [];
                        group.spots.forEach((spot) => {
                            const bc = `${spot?.barcode ?? ""}`.trim();
                            if (bc && isBarcodeStable(bc)) {
                                stableSpots.push(spot);
                            } else {
                                nonStableSpots.push(spot);
                            }
                        });

                        // Sort non-stable spots by barycenter score
                        nonStableSpots.sort((a, b) => {
                            const aBc = `${a?.barcode ?? ""}`.trim();
                            const bBc = `${b?.barcode ?? ""}`.trim();
                            if (!aBc || !bBc) return 0;

                            const aBary = calculateBarycenterScore(
                                a,
                                i,
                                posMaps,
                                "forward",
                            );
                            const bBary = calculateBarycenterScore(
                                b,
                                i,
                                posMaps,
                                "forward",
                            );
                            if (Math.abs(aBary - bBary) > 0.01) {
                                return aBary - bBary;
                            }

                            // Tie-breaker: adjacent stable penalty
                            const aPenalty = calculateAdjacentStablePenalty(
                                a,
                                clusterName,
                                i,
                                clusterSpots.length,
                            );
                            const bPenalty = calculateAdjacentStablePenalty(
                                b,
                                clusterName,
                                i,
                                clusterSpots.length,
                            );
                            if (Math.abs(aPenalty - bPenalty) > 0.001) {
                                return aPenalty - bPenalty;
                            }

                            // Final tie-breaker: stability percent
                            const aStab = getBarcodeStabilityPercent(aBc);
                            const bStab = getBarcodeStabilityPercent(bBc);
                            if (aStab !== bStab) return aStab - bStab;

                            return aBc.localeCompare(bBc, undefined, {
                                numeric: true,
                            });
                        });

                        // Combine: stable spots in middle, non-stable sorted around them
                        const middleIdx = Math.floor(nonStableSpots.length / 2);
                        const orderedNonStable = [
                            ...nonStableSpots.slice(0, middleIdx),
                            ...stableSpots,
                            ...nonStableSpots.slice(middleIdx),
                        ];
                        orderedNonStable.forEach((spot) => {
                            const bc = `${spot?.barcode ?? ""}`.trim();
                            if (bc) orderedBarcodes.push(bc);
                        });
                    });

                    // Update posMaps for this cluster
                    orderedBarcodes.forEach((bc, idx) => {
                        const basePos = posMaps[i].size;
                        posMaps[i].set(bc, basePos + idx);
                    });
                }
            }

            // Backward pass
            for (let i = nRows - 1; i > 0; i--) {
                const rowData = rowVisibleCache[i];
                const clusters = new Map();

                (rowData?.visibleSpots || []).forEach((spot) => {
                    const cluster = String(spot?.cluster ?? "").trim();
                    if (!cluster) return;
                    if (!clusters.has(cluster)) {
                        clusters.set(cluster, []);
                    }
                    clusters.get(cluster).push(spot);
                });

                for (const [clusterName, clusterSpots] of clusters.entries()) {
                    const groups = new Map();
                    if (segmentMode !== "cluster_only") {
                        clusterSpots.forEach((spot) => {
                            const bc = `${spot?.barcode ?? ""}`.trim();
                            if (!bc) return;
                            const trendKey = getTrendKey(spot, bc, i);
                            const key = trendKey || "__default__";
                            if (!groups.has(key)) {
                                groups.set(key, {
                                    key,
                                    sequence: buildSequence(
                                        spot,
                                        bc,
                                        i,
                                        clusterName,
                                    ),
                                    spots: [],
                                    isAdjStableGroup: key === ADJ_STABLE_KEY,
                                });
                            }
                            groups.get(key).spots.push(spot);
                        });
                    } else {
                        groups.set("__cluster_only__", {
                            key: "__cluster_only__",
                            sequence: [clusterName],
                            spots: clusterSpots,
                            isAdjStableGroup: false,
                        });
                    }

                    const groupArray = Array.from(groups.entries()).map(
                        ([key, group]) => ({
                            key,
                            group,
                        }),
                    );

                    groupArray.forEach(({ group }) => {
                        let groupScore = 0;
                        let count = 0;
                        group.spots.forEach((spot) => {
                            const baryPrev = calculateBarycenterScore(
                                spot,
                                i,
                                posMaps,
                                "backward",
                            );
                            groupScore += baryPrev;
                            count++;
                        });
                        group._score = count > 0 ? groupScore / count : 0;
                    });

                    groupArray.sort((a, b) => {
                        const aAllStable = a.group.spots.every((s) => {
                            const bc = `${s?.barcode ?? ""}`.trim();
                            return bc && isBarcodeStable(bc);
                        });
                        const bAllStable = b.group.spots.every((s) => {
                            const bc = `${s?.barcode ?? ""}`.trim();
                            return bc && isBarcodeStable(bc);
                        });
                        if (aAllStable !== bAllStable) {
                            return aAllStable ? -1 : 1;
                        }

                        if (Math.abs(a.group._score - b.group._score) > 0.01) {
                            return a.group._score - b.group._score;
                        }

                        const otherGroups = groupArray
                            .filter((g) => g.key !== a.key && g.key !== b.key)
                            .map((g) => g.group);
                        const aPenalty = calculateTrendSimilarityPenalty(
                            a.group,
                            otherGroups,
                        );
                        const bPenalty = calculateTrendSimilarityPenalty(
                            b.group,
                            otherGroups,
                        );
                        if (Math.abs(aPenalty - bPenalty) > 0.001) {
                            return aPenalty - bPenalty;
                        }

                        return String(a.key).localeCompare(String(b.key));
                    });

                    const orderedBarcodes = [];
                    groupArray.forEach(({ group }) => {
                        const stableSpots = [];
                        const nonStableSpots = [];
                        group.spots.forEach((spot) => {
                            const bc = `${spot?.barcode ?? ""}`.trim();
                            if (bc && isBarcodeStable(bc)) {
                                stableSpots.push(spot);
                            } else {
                                nonStableSpots.push(spot);
                            }
                        });

                        nonStableSpots.sort((a, b) => {
                            const aBc = `${a?.barcode ?? ""}`.trim();
                            const bBc = `${b?.barcode ?? ""}`.trim();
                            if (!aBc || !bBc) return 0;

                            const aBary = calculateBarycenterScore(
                                a,
                                i,
                                posMaps,
                                "backward",
                            );
                            const bBary = calculateBarycenterScore(
                                b,
                                i,
                                posMaps,
                                "backward",
                            );
                            if (Math.abs(aBary - bBary) > 0.01) {
                                return aBary - bBary;
                            }

                            const aPenalty = calculateAdjacentStablePenalty(
                                a,
                                clusterName,
                                i,
                                clusterSpots.length,
                            );
                            const bPenalty = calculateAdjacentStablePenalty(
                                b,
                                clusterName,
                                i,
                                clusterSpots.length,
                            );
                            if (Math.abs(aPenalty - bPenalty) > 0.001) {
                                return aPenalty - bPenalty;
                            }

                            const aStab = getBarcodeStabilityPercent(aBc);
                            const bStab = getBarcodeStabilityPercent(bBc);
                            if (aStab !== bStab) return aStab - bStab;

                            return aBc.localeCompare(bBc, undefined, {
                                numeric: true,
                            });
                        });

                        const middleIdx = Math.floor(nonStableSpots.length / 2);
                        const orderedNonStable = [
                            ...nonStableSpots.slice(0, middleIdx),
                            ...stableSpots,
                            ...nonStableSpots.slice(middleIdx),
                        ];
                        orderedNonStable.forEach((spot) => {
                            const bc = `${spot?.barcode ?? ""}`.trim();
                            if (bc) orderedBarcodes.push(bc);
                        });
                    });

                    orderedBarcodes.forEach((bc, idx) => {
                        const basePos = posMaps[i].size;
                        posMaps[i].set(bc, basePos + idx);
                    });
                }
            }
        }

        // Step C: Build rowOrders output
        const rowOrders = [];
        for (let i = 0; i < nRows; i++) {
            const clusterOrders = new Map();
            const groupOrders = new Map();
            const groupSpotOrders = new Map();

            const rowData = rowVisibleCache[i];
            const clusters = new Map();

            (rowData?.visibleSpots || []).forEach((spot) => {
                const cluster = String(spot?.cluster ?? "").trim();
                if (!cluster) return;
                if (!clusters.has(cluster)) {
                    clusters.set(cluster, []);
                }
                clusters.get(cluster).push(spot);
            });

            for (const [clusterName, clusterSpots] of clusters.entries()) {
                // Build groups
                const groups = new Map();
                if (segmentMode !== "cluster_only") {
                    clusterSpots.forEach((spot) => {
                        const bc = `${spot?.barcode ?? ""}`.trim();
                        if (!bc) return;
                        const trendKey = getTrendKey(spot, bc, i);
                        const key = trendKey || "__default__";
                        if (!groups.has(key)) {
                            // Build sequence based on segmentMode
                            let sequence = [];
                            if (segmentMode === "cluster_set") {
                                sequence = spot?.clusterSet || [];
                            } else if (segmentMode === "trend_sequence") {
                                sequence = spot?.trendSequence || [];
                            } else if (segmentMode === "next_cluster") {
                                // For next_cluster mode, sequence is [currentCluster, nextCluster]
                                if (i === nRows - 1) {
                                    const prevMap =
                                        rowVisibleCache[i - 1]
                                            ?.barcodeClusterMap;
                                    const prevC = prevMap?.get(bc);
                                    sequence = [
                                        prevC ? `${prevC}`.trim() : "?",
                                        clusterName,
                                    ];
                                } else {
                                    const nextMap =
                                        rowVisibleCache[i + 1]
                                            ?.barcodeClusterMap;
                                    const nextC = nextMap?.get(bc);
                                    sequence = [
                                        clusterName,
                                        nextC ? `${nextC}`.trim() : "?",
                                    ];
                                }
                            }
                            groups.set(key, {
                                key,
                                sequence,
                                spots: [],
                                isAdjStableGroup: key === ADJ_STABLE_KEY,
                            });
                        }
                        groups.get(key).spots.push(spot);
                    });
                } else {
                    groups.set("__cluster_only__", {
                        key: "__cluster_only__",
                        sequence: [clusterName],
                        spots: clusterSpots,
                        isAdjStableGroup: false,
                    });
                }

                // Sort groups and spots using final posMaps
                const groupArray = Array.from(groups.entries()).map(
                    ([key, group]) => ({
                        key,
                        group,
                    }),
                );

                groupArray.forEach(({ group }) => {
                    let groupScore = 0;
                    let count = 0;
                    group.spots.forEach((spot) => {
                        const bc = `${spot?.barcode ?? ""}`.trim();
                        if (bc) {
                            const pos = posMaps[i].get(bc);
                            if (pos !== undefined) {
                                groupScore += pos;
                                count++;
                            }
                        }
                    });
                    group._score = count > 0 ? groupScore / count : 0;
                });

                groupArray.sort((a, b) => {
                    if (Math.abs(a.group._score - b.group._score) > 0.01) {
                        return a.group._score - b.group._score;
                    }
                    return String(a.key).localeCompare(String(b.key));
                });

                const groupKeys = [];
                const orderedBarcodes = [];

                groupArray.forEach(({ key, group }) => {
                    groupKeys.push(key);
                    const groupBarcodes = [];

                    const stableSpots = [];
                    const nonStableSpots = [];
                    group.spots.forEach((spot) => {
                        const bc = `${spot?.barcode ?? ""}`.trim();
                        if (!bc) return;
                        if (isBarcodeStable(bc)) {
                            stableSpots.push(spot);
                        } else {
                            nonStableSpots.push(spot);
                        }
                    });

                    nonStableSpots.sort((a, b) => {
                        const aBc = `${a?.barcode ?? ""}`.trim();
                        const bBc = `${b?.barcode ?? ""}`.trim();
                        if (!aBc || !bBc) return 0;
                        const aPos = posMaps[i].get(aBc) ?? 0;
                        const bPos = posMaps[i].get(bBc) ?? 0;
                        if (aPos !== bPos) return aPos - bPos;
                        return aBc.localeCompare(bBc, undefined, {
                            numeric: true,
                        });
                    });

                    const middleIdx = Math.floor(nonStableSpots.length / 2);
                    const orderedNonStable = [
                        ...nonStableSpots.slice(0, middleIdx),
                        ...stableSpots,
                        ...nonStableSpots.slice(middleIdx),
                    ];
                    orderedNonStable.forEach((spot) => {
                        const bc = `${spot?.barcode ?? ""}`.trim();
                        if (bc) {
                            groupBarcodes.push(bc);
                            orderedBarcodes.push(bc);
                        }
                    });

                    groupSpotOrders.set(key, groupBarcodes);
                });

                clusterOrders.set(clusterName, orderedBarcodes);
                groupOrders.set(clusterName, groupKeys);
            }

            rowOrders.push({
                clusterOrders,
                groupOrders,
                groupSpotOrders,
            });
        }

        return { rowOrders, posMaps };
    }

    function colorWithAlpha(color, alpha = 0.35) {
        if (!color) return `rgba(148, 163, 184, ${alpha})`;

        const colorStr = String(color).trim();

        const rgbaMatch = colorStr.match(
            /rgba?\((\d+),\s*(\d+),\s*(\d+)(?:,\s*[\d.]+)?\)/,
        );
        if (rgbaMatch) {
            return `rgba(${rgbaMatch[1]}, ${rgbaMatch[2]}, ${rgbaMatch[3]}, ${alpha})`;
        }

        const rgbMatch = colorStr.match(/rgb\((\d+),\s*(\d+),\s*(\d+)\)/);
        if (rgbMatch) {
            return `rgba(${rgbMatch[1]}, ${rgbMatch[2]}, ${rgbMatch[3]}, ${alpha})`;
        }

        const hexMatch = colorStr.match(/^#([0-9A-Fa-f]{3}|[0-9A-Fa-f]{6})$/);
        if (hexMatch) {
            let hex = hexMatch[1];
            if (hex.length === 3) {
                hex = hex
                    .split("")
                    .map((c) => c + c)
                    .join("");
            }
            const r = parseInt(hex.substring(0, 2), 16);
            const g = parseInt(hex.substring(2, 4), 16);
            const b = parseInt(hex.substring(4, 6), 16);
            return `rgba(${r}, ${g}, ${b}, ${alpha})`;
        }

        try {
            const ctx = document.createElement("canvas").getContext("2d");
            ctx.fillStyle = colorStr;
            const computed = ctx.fillStyle;
            const rgbMatch2 = computed.match(/rgb\((\d+),\s*(\d+),\s*(\d+)\)/);
            if (rgbMatch2) {
                return `rgba(${rgbMatch2[1]}, ${rgbMatch2[2]}, ${rgbMatch2[3]}, ${alpha})`;
            }
        } catch (e) {
            // Ignore parsing errors
        }

        return `rgba(148, 163, 184, ${alpha})`;
    }

    function scheduleMeasure() {
        if (measureQueued) return;
        measureQueued = true;
        measureRaf = window.requestAnimationFrame(() => {
            measureQueued = false;
            measureConnections();
        });
    }

    function measureConnections() {
        if (
            !renderCanvas ||
            !connectionWrapper ||
            !visibleDistributionSummary?.length
        ) {
            layoutCache = [];
            clearCanvas();
            return;
        }

        const wrapperRect = connectionWrapper.getBoundingClientRect();
        // 确保 canvas 尺寸不超过容器的实际可视尺寸
        const visibleWidth = Math.max(
            1,
            Math.min(
                wrapperRect.width,
                connectionWrapper.clientWidth || wrapperRect.width,
            ),
        );
        const containerHeight = Math.max(
            1,
            Math.min(
                wrapperRect.height,
                connectionWrapper.clientHeight || wrapperRect.height,
            ),
        );

        const dpr =
            typeof window !== "undefined" ? window.devicePixelRatio || 1 : 1;
        const CARD_HEIGHT = 80; // 纵向布局时每行的高度
        const CARD_GAP = 10;
        // Margins to accommodate highlight border and avoid left edge being cut off
        // After 90° rotation, LEADING_MARGIN becomes the left margin on screen
        const LEADING_MARGIN = 30;
        const TRAILING_MARGIN = 10;
        const minRowHeight = 8;
        const preferredRowHeight = 20;

        const rowCount = visibleDistributionSummary.length;

        // After 90° rotation: swap the logical dimensions
        // drawWidth (logical width, becomes screen height after rotation) should use container height
        // drawHeight (logical height, becomes screen width after rotation) should use container width
        const drawWidth = containerHeight; // Swapped: container height becomes logical width
        const drawHeight = visibleWidth; // Swapped: container width becomes logical height

        const rowHeight = Math.max(
            minRowHeight,
            Math.min(preferredRowHeight, CARD_HEIGHT * 0.25),
        );

        // Calculate row positions to fill the canvas while leaving space for highlight border
        // First row starts near top (with margin), last row ends near bottom (with margin)
        let rowSpacing;
        let firstRowCenterY;
        if (rowCount === 0) {
            rowSpacing = 0;
            firstRowCenterY = 0;
        } else if (rowCount === 1) {
            rowSpacing = 0;
            firstRowCenterY = drawHeight / 2;
        } else {
            // First row center at LEADING_MARGIN + rowHeight/2, last row center at drawHeight - TRAILING_MARGIN - rowHeight/2
            firstRowCenterY = LEADING_MARGIN + rowHeight / 2;
            const lastRowCenterY = drawHeight - TRAILING_MARGIN - rowHeight / 2;
            rowSpacing = (lastRowCenterY - firstRowCenterY) / (rowCount - 1);
        }

        // Rotate 90°: swap canvas physical dimensions so drawing in (drawWidth, drawHeight)
        // appears as (drawHeight, drawWidth) on screen
        const canvasWidth = Math.floor(drawHeight * dpr); // Swapped: height becomes width
        const canvasHeight = Math.floor(drawWidth * dpr); // Swapped: width becomes height

        if (
            renderCanvas.width !== canvasWidth ||
            renderCanvas.height !== canvasHeight
        ) {
            renderCanvas.width = canvasWidth;
            renderCanvas.height = canvasHeight;
        }
        // Display size: after rotation, canvas display size should match container size
        // drawHeight (logical) becomes screen width, drawWidth (logical) becomes screen height
        renderCanvas.style.width = `${drawHeight}px`; // = visibleWidth, matches container width
        renderCanvas.style.height = `${drawWidth}px`; // = containerHeight, matches container height
        // 确保 canvas 尺寸根据容器自适应，由容器的 overflow 控制滚动
        renderCanvas.style.minHeight = "0";
        renderCanvas.style.flexShrink = "0";

        const ctx = renderCanvas.getContext("2d");
        if (!ctx) return;
        ctx.setTransform(1, 0, 0, 1, 0, 0);
        ctx.clearRect(0, 0, canvasWidth, canvasHeight);
        ctx.scale(dpr, dpr);
        // Rotate 90° clockwise: translate then rotate
        // After rotation, original (x, y) maps to screen (y, drawWidth - x)
        ctx.translate(drawHeight, 0);
        ctx.rotate(Math.PI / 2);
        // Note: We don't apply a global columnOffset here because we want
        // only the first column to shift right, with the last column staying in place.
        // The offset will be applied per-row in rowPositions calculation.
        // Enable high-quality image smoothing for better rendering quality
        ctx.imageSmoothingEnabled = true;
        if (ctx.imageSmoothingQuality) {
            ctx.imageSmoothingQuality = "high";
        }

        const paddingLeft = 24; // Restored to original value since we use columnOffset instead
        const paddingRight = 24;
        // Progressive offset: first column shifts right, last column stays in place
        const columnOffset = 20; // Amount to shift the first column to the right
        const sankeyAreaWidth = Math.max(
            8,
            drawWidth - paddingLeft - paddingRight,
        );
        const rowWidth = sankeyAreaWidth;
        const rowLeft = paddingLeft;

        const rowLayouts = [];

        // Precompute visible spots + barcode->cluster maps per row for fast "adjacent stable" checks.
        // Each row checks focusedClusterByRow[rowIndex] to determine if it should show only one cluster.
        const rowVisible = visibleDistributionSummary.map(
            (summary, rowIndex) => {
                const baseSpots = Array.isArray(summary?.spots)
                    ? summary.spots
                    : [];
                const visibleSpots = baseSpots.filter((spot) => {
                    const bc =
                        spot?.barcode === null || spot?.barcode === undefined
                            ? ""
                            : `${spot.barcode}`.trim();
                    if (!bc) return false;
                    return !isBarcodeFilteredOutByStability(bc);
                });
                const focusClusterRaw = focusedClusterByRow?.[rowIndex];
                const focusCluster =
                    focusClusterRaw === null || focusClusterRaw === undefined
                        ? null
                        : String(focusClusterRaw).trim();
                // Only filter to focusCluster if this specific row has a focused cluster
                // Other rows remain unaffected (show all spots)
                let focusedVisibleSpots = focusCluster
                    ? visibleSpots.filter(
                          (s) =>
                              String(s?.cluster ?? "").trim() === focusCluster,
                      )
                    : visibleSpots;
                const barcodeClusterMap = new Map();
                for (const spot of focusedVisibleSpots) {
                    const bc =
                        spot?.barcode === null || spot?.barcode === undefined
                            ? ""
                            : `${spot.barcode}`.trim();
                    if (!bc) continue;
                    barcodeClusterMap.set(bc, String(spot.cluster).trim());
                }
                return {
                    visibleSpots: focusedVisibleSpots,
                    visibleTotal: focusedVisibleSpots.length,
                    focusCluster,
                    barcodeClusterMap,
                };
            },
        );
        // keep for hover/highlight logic outside measureConnections
        rowVisibleCache = rowVisible;

        // Compute row orders using barycenter algorithm
        let globalBarcodeRank = null;
        if (flowMode === "individual" && activeFocusRank) {
            globalBarcodeRank = activeFocusRank;
        } else if (flowMode === "individual" && activeFocus) {
            const focusRowIndex = Number.isFinite(activeFocus.rowIndex)
                ? activeFocus.rowIndex
                : -1;
            const focusCluster = String(activeFocus.clusterName ?? "").trim();
            if (
                focusRowIndex >= 0 &&
                focusRowIndex < rowVisible.length &&
                focusCluster
            ) {
                globalBarcodeRank = new Map();
                const focusSpots =
                    rowVisible[focusRowIndex]?.visibleSpots || [];
                let i = 0;
                for (const s of focusSpots) {
                    const bc =
                        s?.barcode === null || s?.barcode === undefined
                            ? ""
                            : `${s.barcode}`.trim();
                    if (!bc || isBarcodeFilteredOutByStability(bc)) continue;
                    if (String(s?.cluster ?? "").trim() !== focusCluster)
                        continue;
                    if (!globalBarcodeRank.has(bc)) {
                        globalBarcodeRank.set(bc, i);
                        i += 1;
                    }
                }
                if (globalBarcodeRank.size === 0) {
                    globalBarcodeRank = null;
                }
            }
        }

        const { rowOrders, posMaps } = computeRowOrders(rowVisible, {
            segmentMode,
            flowMode,
            nIters: 4,
            alpha: 0.7,
            beta: 0.15,
            gamma: 0.2,
            globalBarcodeRank,
        });

        // Note: rowOrders is now the single source of truth for ordering
        // No more left/middle/right splitting - use rowOrders directly

        // Find selected row index by position in visibleDistributionSummary
        // Treat "default" as no selection
        const selectedRowIndex =
            selectedResultId && selectedResultId !== "default"
                ? visibleDistributionSummary.findIndex(
                      (s) => s.result?.cluster_result_id === selectedResultId,
                  )
                : -1;

        // Number of rows/columns
        const numRows = visibleDistributionSummary.length;
        
        // Draw each row (no per-row x offset here; horizontal layout handled by rowLeft/rowWidth)
        visibleDistributionSummary.forEach((summary, idx) => {
            const total = summary.total ?? 0;
            // Calculate row center position to fill the entire canvas
            const rowCenterY = firstRowCenterY + idx * rowSpacing;
            const rowTop = rowCenterY - rowHeight / 2;
            const isSelectedRow =
                selectedRowIndex >= 0 && idx === selectedRowIndex;

            // Row highlight with border for selected result
            if (isSelectedRow) {
                ctx.save();
                const borderOffset = 2;
                const borderX = rowLeft - borderOffset;
                const borderY = rowTop - borderOffset;
                const borderW = rowWidth + borderOffset * 2;
                const borderH = rowHeight + borderOffset * 2;

                // Draw subtle background tint first
                ctx.fillStyle = "rgba(71, 85, 105, 0.1)"; // slate-600 with low alpha
                ctx.fillRect(borderX, borderY, borderW, borderH);

                // Draw complete rectangle border
                ctx.strokeStyle = "#475569"; // slate-600
                ctx.lineWidth = 3;
                ctx.strokeRect(borderX, borderY, borderW, borderH);

                ctx.restore();
            }
            const layout = {
                summary,
                layoutIndex: idx,
                x: rowLeft,
                y: rowTop,
                width: rowWidth,
                height: rowHeight,
                clusterRects: [],
                barcodeMap: new Map(),
                total,
            };

            if (total > 0) {
                // Apply stability threshold in Sankey bar rendering
                const visibleSpots = rowVisible[idx].visibleSpots;
                const visibleTotal = rowVisible[idx].visibleTotal;
                layout.total = visibleTotal;

                let cursorX = rowLeft;
                const focusCluster = rowVisible[idx]?.focusCluster || null;
                summary.clusters.forEach((cluster) => {
                    if (!cluster.count) return;
                    const clusterName = String(cluster.cluster).trim();
                    if (
                        focusCluster &&
                        clusterName !== String(focusCluster).trim()
                    )
                        return;

                    // Group visible spots in this cluster by trend
                    const clusterSpots = visibleSpots.filter(
                        (spot) => String(spot.cluster).trim() === clusterName,
                    );
                    const visibleClusterCount = clusterSpots.length;
                    if (!visibleClusterCount || !visibleTotal) return;

                    // Scale cluster width by visible spots so the remaining bars fill the whole width
                    const clusterWidth =
                        (visibleClusterCount / visibleTotal) * rowWidth;

                    const adjStableSpots = [];
                    const otherSpots = [];
                    for (const spot of clusterSpots) {
                        const bc =
                            spot?.barcode === null ||
                            spot?.barcode === undefined
                                ? ""
                                : `${spot.barcode}`.trim();
                        if (!bc) continue;
                        if (
                            isAdjacentStableAtRow(
                                rowVisible,
                                idx,
                                bc,
                                clusterName,
                            )
                        ) {
                            adjStableSpots.push(spot);
                        } else {
                            otherSpots.push(spot);
                        }
                    }

                    const trendGroups = new Map();
                    if (
                        segmentMode !== "next_cluster" &&
                        segmentMode !== "cluster_only" &&
                        adjStableSpots.length > 0
                    ) {
                        trendGroups.set(ADJ_STABLE_KEY, {
                            key: ADJ_STABLE_KEY,
                            isAdjStableGroup: true,
                            sequence: [clusterName],
                            spots: adjStableSpots,
                        });
                    }

                    const spotsForGrouping =
                        segmentMode === "next_cluster" ||
                        segmentMode === "cluster_only"
                            ? clusterSpots
                            : otherSpots;

                    spotsForGrouping.forEach((spot) => {
                        let key = null;
                        let sequence = [];
                        const bc = `${spot?.barcode ?? ""}`.trim();
                        const nRows = rowVisible.length;

                        if (segmentMode === "cluster_only") {
                            key = "__cluster_only__";
                            sequence = [];
                        } else if (segmentMode === "next_cluster") {
                            if (idx === nRows - 1) {
                                // Last row: group by previous cluster
                                if (idx <= 0) {
                                    key = "Start";
                                } else {
                                    const prevMap =
                                        rowVisible[idx - 1]?.barcodeClusterMap;
                                    if (!prevMap) {
                                        key = "Gone";
                                    } else {
                                        const prevC = prevMap.get(bc);
                                        key =
                                            prevC === null ||
                                            prevC === undefined ||
                                            `${prevC}`.trim() === ""
                                                ? "Gone"
                                                : `${prevC}`.trim();
                                    }
                                }
                                sequence = [key, clusterName];
                            } else {
                                // Not last row: group by next cluster
                                if (idx >= nRows - 1) {
                                    key = "End";
                                } else {
                                    const nextMap =
                                        rowVisible[idx + 1]?.barcodeClusterMap;
                                    if (!nextMap) {
                                        key = "Gone";
                                    } else {
                                        const nextC = nextMap.get(bc);
                                        key =
                                            nextC === null ||
                                            nextC === undefined ||
                                            `${nextC}`.trim() === ""
                                                ? "Gone"
                                                : `${nextC}`.trim();
                                    }
                                }
                                sequence = [clusterName, key];
                            }
                        } else if (segmentMode === "cluster_set") {
                            key = spot.clusterSetKey || "__default__";
                            sequence = spot.clusterSet || [];
                        } else {
                            // "trend_sequence"
                            key = spot.trendKey || "__default__";
                            sequence = spot.trendSequence || [];
                        }

                        // Use __default__ fallback for empty keys (consistent with computeRowOrders)
                        const finalKey = key || "__default__";
                        if (!trendGroups.has(finalKey)) {
                            trendGroups.set(finalKey, {
                                key: finalKey,
                                sequence,
                                spots: [],
                            });
                        }
                        trendGroups.get(finalKey).spots.push(spot);
                    });

                    // Use rowOrders as the single source of truth for ordering
                    // No more complex sorting - just use the computed order
                    const rowOrder = rowOrders[idx];
                    const orderedBarcodes =
                        rowOrder?.clusterOrders?.get(clusterName) || [];

                    // Build a map from barcode to spot for quick lookup
                    const barcodeToSpot = new Map();
                    clusterSpots.forEach((spot) => {
                        const bc = `${spot?.barcode ?? ""}`.trim();
                        if (bc) barcodeToSpot.set(bc, spot);
                    });

                    // Reorder spots according to rowOrders
                    const reorderedClusterSpots = [];
                    orderedBarcodes.forEach((bc) => {
                        const spot = barcodeToSpot.get(bc);
                        if (spot) reorderedClusterSpots.push(spot);
                    });

                    // Add any spots not in orderedBarcodes (edge cases)
                    clusterSpots.forEach((spot) => {
                        const bc = `${spot?.barcode ?? ""}`.trim();
                        if (bc && !orderedBarcodes.includes(bc)) {
                            reorderedClusterSpots.push(spot);
                        }
                    });

                    // Build trendRects from rowOrders group information
                    const trendRects = [];
                    let trendCursorX = cursorX;
                    const groupKeys =
                        rowOrder?.groupOrders?.get(clusterName) || [];

                    groupKeys.forEach((groupKey) => {
                        const groupBarcodes =
                            rowOrder?.groupSpotOrders?.get(groupKey) || [];
                        const groupSpots = groupBarcodes
                            .map((bc) => barcodeToSpot.get(bc))
                            .filter((s) => s);
                        const trendCount = groupSpots.length;

                        if (trendCount > 0 && segmentMode !== "cluster_only") {
                            const trendWidth =
                                (trendCount / visibleClusterCount) *
                                clusterWidth;
                            // Find the group's sequence from trendGroups
                            const trendGroup = trendGroups.get(groupKey);
                            trendRects.push({
                                x: trendCursorX,
                                y: rowTop,
                                width: trendWidth,
                                height: rowHeight,
                                trendKey: groupKey,
                                trendSequence: trendGroup?.sequence || [],
                                count: trendCount,
                                percent:
                                    (trendCount / visibleClusterCount) * 100,
                                isAdjStableGroup: groupKey === ADJ_STABLE_KEY,
                            });
                            trendCursorX += trendWidth;
                        }
                    });

                    // Store sorted spots for this cluster to use when reordering summary.spots later
                    if (!layout._clusterSpotOrder) {
                        layout._clusterSpotOrder = new Map();
                    }
                    layout._clusterSpotOrder.set(
                        clusterName,
                        reorderedClusterSpots,
                    );

                    // In individual mode, create flow-based segments within each cluster bar
                    // When segmentMode is "next_cluster", flowRects should match trendRects order
                    let flowRects = [];
                    if (
                        flowMode === "individual" &&
                        idx < rowVisible.length - 1
                    ) {
                        const nextMapForFlow =
                            rowVisible[idx + 1].barcodeClusterMap;
                        const flowGroups = new Map(); // toCluster -> spots[]

                        for (const spot of reorderedClusterSpots) {
                            const bc = `${spot?.barcode ?? ""}`.trim();
                            if (!bc) continue;
                            const toCluster = nextMapForFlow?.get(bc) || "";
                            if (!toCluster) continue;

                            if (!flowGroups.has(toCluster)) {
                                flowGroups.set(toCluster, []);
                            }
                            flowGroups.get(toCluster).push(spot);
                        }

                        // When segmentMode is "next_cluster", use groupKeys order (matches trendRects)
                        // Otherwise, sort by cluster label
                        let sortedToClusters;
                        if (
                            segmentMode === "next_cluster" &&
                            groupKeys.length > 0
                        ) {
                            // Use the same order as trendRects (groupKeys from rowOrders)
                            sortedToClusters = groupKeys.filter((key) =>
                                flowGroups.has(key),
                            );
                            // Add any extra clusters not in groupKeys
                            const extraClusters = Array.from(flowGroups.keys())
                                .filter((k) => !groupKeys.includes(k))
                                .sort((a, b) => compareClusterLabel(a, b));
                            sortedToClusters = [
                                ...sortedToClusters,
                                ...extraClusters,
                            ];
                        } else {
                            sortedToClusters = Array.from(
                                flowGroups.keys(),
                            ).sort((a, b) => compareClusterLabel(a, b));
                        }

                        let flowCursorX = cursorX;
                        for (const toCluster of sortedToClusters) {
                            const flowSpots = flowGroups.get(toCluster);
                            const flowCount = flowSpots.length;
                            const flowWidth =
                                (flowCount / visibleClusterCount) *
                                clusterWidth;

                            flowRects.push({
                                x: flowCursorX,
                                y: rowTop,
                                width: flowWidth,
                                height: rowHeight,
                                fromCluster: clusterName,
                                toCluster: toCluster,
                                count: flowCount,
                                percent:
                                    (flowCount / visibleClusterCount) * 100,
                            });
                            flowCursorX += flowWidth;
                        }
                    }

                    layout.clusterRects.push({
                        x: cursorX,
                        y: rowTop,
                        width: clusterWidth,
                        height: rowHeight,
                        color: cluster.color,
                        cluster: cluster.cluster,
                        count: visibleClusterCount,
                        percent: (visibleClusterCount / visibleTotal) * 100,
                        trendRects, // Add trend rects
                        flowRects, // Add flow rects for individual mode
                    });
                    cursorX += clusterWidth;
                });

                // Build ordered visible spot list (do NOT mutate summary.spots)
                const orderedVisibleSpots = [];
                if (
                    layout._clusterSpotOrder &&
                    layout._clusterSpotOrder.size > 0
                ) {
                    summary.clusters.forEach((cluster) => {
                        const clusterName = String(cluster.cluster).trim();
                        const sortedSpots =
                            layout._clusterSpotOrder.get(clusterName) || [];
                        orderedVisibleSpots.push(...sortedSpots);
                    });
                }
                const addedBarcodes = new Set(
                    orderedVisibleSpots.map((s) =>
                        s?.barcode === null || s?.barcode === undefined
                            ? ""
                            : `${s.barcode}`.trim(),
                    ),
                );
                visibleSpots.forEach((spot) => {
                    const bc =
                        spot?.barcode === null || spot?.barcode === undefined
                            ? ""
                            : `${spot.barcode}`.trim();
                    if (bc && !addedBarcodes.has(bc))
                        orderedVisibleSpots.push(spot);
                });

                // Establish global barcode rank from the FIRST row's current order
                if (idx === 0 && !globalBarcodeRank) {
                    globalBarcodeRank = new Map();
                    orderedVisibleSpots.forEach((spot, i) => {
                        const bc =
                            spot?.barcode === null ||
                            spot?.barcode === undefined
                                ? ""
                                : `${spot.barcode}`.trim();
                        if (bc && !globalBarcodeRank.has(bc)) {
                            globalBarcodeRank.set(bc, i);
                        }
                    });
                }

                // Calculate x positions based on actual trend positions
                // Use the sorted cluster spots order stored in layout._clusterSpotOrder
                const spotXMap = new Map(); // barcode -> x position (for this summary)

                // Get the rowOrder for this row (defined at this scope level, outside the cluster loops)
                const rowOrder = rowOrders[idx];

                // Calculate x positions for each trend group using the sorted order
                layout.clusterRects.forEach((rect) => {
                    if (
                        segmentMode === "cluster_only" &&
                        layout._clusterSpotOrder
                    ) {
                        const clusterName = String(rect.cluster).trim();
                        const sortedClusterSpots =
                            layout._clusterSpotOrder.get(clusterName) || [];
                        const n = sortedClusterSpots.length;
                        if (!n) return;
                        for (let i = 0; i < n; i += 1) {
                            const spot = sortedClusterSpots[i];
                            const barcode = `${spot?.barcode ?? ""}`.trim();
                            if (!barcode) continue;
                            const spotWidth = rect.width / n;
                            const x = rect.x + (i + 0.5) * spotWidth;
                            spotXMap.set(barcode, x);
                        }
                        return;
                    }

                    if (
                        rect.trendRects &&
                        rect.trendRects.length > 0 &&
                        rowOrder
                    ) {
                        const clusterName = String(rect.cluster).trim();

                        // Use groupSpotOrders from rowOrders directly - no need to re-filter/re-sort
                        // This ensures spots are positioned in the order computed by computeRowOrders (barycenter)
                        rect.trendRects.forEach((trendRect) => {
                            const trendKey = trendRect.trendKey;
                            // Get spots for this group from rowOrders (already in correct order)
                            const groupBarcodes =
                                rowOrder.groupSpotOrders?.get(trendKey) || [];

                            // Build a spot lookup for this cluster
                            const sortedClusterSpots =
                                layout._clusterSpotOrder?.get(clusterName) ||
                                [];
                            const barcodeToSpotLocal = new Map();
                            sortedClusterSpots.forEach((spot) => {
                                const bc = `${spot?.barcode ?? ""}`.trim();
                                if (bc) barcodeToSpotLocal.set(bc, spot);
                            });

                            // Get spots in the order from rowOrders
                            const trendSpots = groupBarcodes
                                .map((bc) => barcodeToSpotLocal.get(bc))
                                .filter((s) => s);

                            // Calculate x position for each spot within this trend
                            trendSpots.forEach((spot, trendIdx) => {
                                const trendCount = trendSpots.length;
                                if (trendCount === 0) return;
                                const spotWidth = trendRect.width / trendCount;
                                const x =
                                    trendRect.x + (trendIdx + 0.5) * spotWidth;
                                const barcode = `${spot.barcode}`.trim();
                                spotXMap.set(barcode, x);
                            });
                        });
                    }
                });

                // Use calculated x positions or fallback to index-based positioning
                orderedVisibleSpots.forEach((spot, idx) => {
                    const barcode = `${spot.barcode}`.trim();
                    let centerX = spotXMap.get(barcode);
                    if (centerX === undefined) {
                        centerX =
                            visibleTotal > 0
                                ? rowLeft +
                                  ((idx + 0.5) / visibleTotal) * rowWidth
                                : rowLeft + rowWidth / 2;
                    }
                    const entry = {
                        x: centerX,
                        color: spot.color || "rgba(148, 163, 184, 0.45)",
                        cluster: spot.cluster,
                    };
                    if (!layout.barcodeMap.has(barcode)) {
                        layout.barcodeMap.set(barcode, []);
                    }
                    layout.barcodeMap.get(barcode).push(entry);
                });
            }

            rowLayouts.push(layout);
        });

        ctx.save();
        // Ensure high-quality rendering
        ctx.imageSmoothingEnabled = true;
        if (ctx.imageSmoothingQuality) {
            ctx.imageSmoothingQuality = "high";
        }
        ctx.lineCap = "round";
        ctx.lineJoin = "round";

        const connectionEntries = [];

        // Check if there's any highlight active
        const hasHighlight =
            highlightedBarcodeFromPreview !== null ||
            highlightedBarcodeFromSankey !== null;

        // First pass: count all visible connections to determine line width and alpha
        let totalVisibleConnections = 0;
        for (let idx = 0; idx < rowLayouts.length - 1; idx += 1) {
            const top = rowLayouts[idx];
            const bottom = rowLayouts[idx + 1];
            if (!top.summary.total || !bottom.summary.total) continue;

            if (flowMode === "aggregated") {
                const topMap = rowVisible[idx]?.barcodeClusterMap;
                const bottomMap = rowVisible[idx + 1]?.barcodeClusterMap;
                if (!topMap || !bottomMap) continue;

                const flowCounts = new Map();
                for (const [bc, fromClusterRaw] of topMap.entries()) {
                    if (!bc || isBarcodeFilteredOutByStability(bc)) continue;
                    const toClusterRaw = bottomMap.get(bc);
                    if (toClusterRaw === null || toClusterRaw === undefined)
                        continue;
                    const fromCluster = String(fromClusterRaw).trim();
                    const toCluster = String(toClusterRaw).trim();
                    if (!fromCluster || !toCluster) continue;
                    const k = `${fromCluster}||${toCluster}`;
                    flowCounts.set(k, (flowCounts.get(k) || 0) + 1);
                }
                totalVisibleConnections += flowCounts.size;
            } else {
                // Individual mode: count barcodes with connections
                for (const [barcode, topSpots] of top.barcodeMap.entries()) {
                    const bottomSpots = bottom.barcodeMap.get(barcode);
                    if (!bottomSpots?.length) continue;
                    if (isBarcodeFilteredOutByStability(barcode)) continue;
                    const pairCount = Math.min(
                        topSpots.length,
                        bottomSpots.length,
                    );
                    totalVisibleConnections += pairCount;
                }
            }
        }

        // Calculate base line width based on visible connection count
        // More connections = thinner lines (inverse relationship)
        // Use a logarithmic scale to make the adjustment smoother
        let baseLineWidth;
        let baseAlpha;
        if (totalVisibleConnections === 0) {
            baseLineWidth = 1.25; // Default
            baseAlpha = 0.7;
        } else if (totalVisibleConnections <= 10) {
            baseLineWidth = 2.5;
            baseAlpha = 0.85; // 连接线少时更不透明
        } else if (totalVisibleConnections <= 50) {
            baseLineWidth = 1.5;
            baseAlpha = 0.7;
        } else if (totalVisibleConnections <= 100) {
            baseLineWidth = 1.25;
            baseAlpha = 0.55;
        } else if (totalVisibleConnections <= 500) {
            baseLineWidth = 0.75;
            baseAlpha = 0.45;
        } else if (totalVisibleConnections <= 1000) {
            baseLineWidth = 0.5;
            baseAlpha = 0.4;
        } else {
            baseLineWidth = 0.35;
            baseAlpha = 0.35;
        }
        ctx.lineWidth = baseLineWidth;
        // 根据连接数量动态调整透明度：线少时更不透明，线多时更透明
        // aggregated 模式整体更不透明
        ctx.globalAlpha = flowMode === "aggregated" ? Math.min(0.95, baseAlpha + 0.15) : baseAlpha;

        // Second pass: draw connections with calculated line width
        for (let idx = 0; idx < rowLayouts.length - 1; idx += 1) {
            const top = rowLayouts[idx];
            const bottom = rowLayouts[idx + 1];

            if (!top.summary.total || !bottom.summary.total) continue;

            const y1 = top.y + top.height;
            const y2 = bottom.y;
            const gapHeight = Math.max(8, y2 - y1);
            const controlOffset = Math.min(80, gapHeight * 0.45);

            if (flowMode === "aggregated") {
                // Real Sankey-style aggregation:
                // allocate sub-intervals inside each source/destination cluster bar proportional to flow counts,
                // then draw filled ribbon polygons between those intervals.
                const topRectsByCluster = new Map();
                const bottomRectsByCluster = new Map();
                top.clusterRects.forEach((r) => {
                    topRectsByCluster.set(String(r.cluster).trim(), r);
                });
                bottom.clusterRects.forEach((r) => {
                    bottomRectsByCluster.set(String(r.cluster).trim(), r);
                });

                // Count flows by cluster->cluster using barcodeClusterMap (one per barcode per row).
                const topMap = rowVisible[idx]?.barcodeClusterMap;
                const bottomMap = rowVisible[idx + 1]?.barcodeClusterMap;
                if (!topMap || !bottomMap) continue;

                const flowCounts = new Map(); // key "from||to" -> count
                const outTotals = new Map(); // from -> total out
                const inTotals = new Map(); // to -> total in

                for (const [bc, fromClusterRaw] of topMap.entries()) {
                    if (!bc || isBarcodeFilteredOutByStability(bc)) continue;
                    const toClusterRaw = bottomMap.get(bc);
                    if (toClusterRaw === null || toClusterRaw === undefined)
                        continue;
                    const fromCluster = String(fromClusterRaw).trim();
                    const toCluster = String(toClusterRaw).trim();
                    if (!fromCluster || !toCluster) continue;
                    const k = `${fromCluster}||${toCluster}`;
                    flowCounts.set(k, (flowCounts.get(k) || 0) + 1);
                    outTotals.set(
                        fromCluster,
                        (outTotals.get(fromCluster) || 0) + 1,
                    );
                    inTotals.set(toCluster, (inTotals.get(toCluster) || 0) + 1);
                }

                // Precompute ordering of outgoing/incoming bundles to reduce crossings:
                // - within a source cluster: sort destination clusters by cluster label
                // - within a destination cluster: sort source clusters by cluster label
                const outOrder = new Map(); // from -> [to...]
                const inOrder = new Map(); // to -> [from...]
                for (const k of flowCounts.keys()) {
                    const [from, to] = k.split("||");
                    if (!outOrder.has(from)) outOrder.set(from, new Set());
                    outOrder.get(from).add(to);
                    if (!inOrder.has(to)) inOrder.set(to, new Set());
                    inOrder.get(to).add(from);
                }
                for (const [from, set] of outOrder.entries()) {
                    outOrder.set(
                        from,
                        [...set].sort((a, b) => compareClusterLabel(a, b)),
                    );
                }
                for (const [to, set] of inOrder.entries()) {
                    inOrder.set(
                        to,
                        [...set].sort((a, b) => compareClusterLabel(a, b)),
                    );
                }

                // Compute allocated x-intervals for each flow on the source side.
                const srcIntervals = new Map(); // key -> {x0,x1}
                for (const [from, toList] of outOrder.entries()) {
                    const rect = topRectsByCluster.get(from);
                    const total = outTotals.get(from) || 0;
                    if (!rect || !total) continue;
                    let cursor = rect.x;
                    for (const to of toList) {
                        const k = `${from}||${to}`;
                        const c = flowCounts.get(k) || 0;
                        if (!c) continue;
                        const w = (rect.width * c) / total;
                        srcIntervals.set(k, { x0: cursor, x1: cursor + w });
                        cursor += w;
                    }
                }

                // Compute allocated x-intervals for each flow on the destination side.
                const dstIntervals = new Map(); // key -> {x0,x1}
                for (const [to, fromList] of inOrder.entries()) {
                    const rect = bottomRectsByCluster.get(to);
                    const total = inTotals.get(to) || 0;
                    if (!rect || !total) continue;
                    let cursor = rect.x;
                    for (const from of fromList) {
                        const k = `${from}||${to}`;
                        const c = flowCounts.get(k) || 0;
                        if (!c) continue;
                        const w = (rect.width * c) / total;
                        dstIntervals.set(k, { x0: cursor, x1: cursor + w });
                        cursor += w;
                    }
                }

                // Find min and max flow counts for scaling line width
                const flowCountsArray = Array.from(flowCounts.values());
                const minCount =
                    flowCountsArray.length > 0
                        ? Math.min(...flowCountsArray)
                        : 1;
                const maxCount =
                    flowCountsArray.length > 0
                        ? Math.max(...flowCountsArray)
                        : 1;
                const countRange = Math.max(1, maxCount - minCount);

                // Draw each ribbon polygon + cache a centerline for hit testing.
                for (const [k, count] of flowCounts.entries()) {
                    if (!count) continue;
                    const [fromCluster, toCluster] = k.split("||");
                    const src = srcIntervals.get(k);
                    const dst = dstIntervals.get(k);
                    const srcRect = topRectsByCluster.get(fromCluster);
                    const dstRect = bottomRectsByCluster.get(toCluster);
                    if (!src || !dst || !srcRect || !dstRect) continue;

                    const sx0 = src.x0;
                    const sx1 = src.x1;
                    const tx0 = dst.x0;
                    const tx1 = dst.x1;

                    // Use source cluster color for the band.
                    const bandColor =
                        srcRect.color || "rgba(148, 163, 184, 0.45)";

                    ctx.beginPath();
                    // left edge
                    ctx.moveTo(sx0, y1);
                    ctx.bezierCurveTo(
                        sx0,
                        y1 + controlOffset,
                        tx0,
                        y2 - controlOffset,
                        tx0,
                        y2,
                    );
                    // bottom edge (to right)
                    ctx.lineTo(tx1, y2);
                    // right edge back
                    ctx.bezierCurveTo(
                        tx1,
                        y2 - controlOffset,
                        sx1,
                        y1 + controlOffset,
                        sx1,
                        y1,
                    );
                    ctx.closePath();
                    ctx.fillStyle = colorWithAlpha(bandColor, 0.65);
                    ctx.fill();
                    // Subtle outline helps readability when ribbons overlap
                    // Line width scales with flow count: larger flows get thicker lines
                    // Scale from 0.3 to 1.5 times baseLineWidth based on count
                    const countRatio =
                        countRange > 0 ? (count - minCount) / countRange : 0.5;
                    const lineWidthScale = 0.3 + countRatio * 1.2; // Range: 0.3 to 1.5
                    ctx.lineWidth = Math.max(
                        0.3,
                        baseLineWidth * lineWidthScale,
                    );
                    ctx.strokeStyle = colorWithAlpha(bandColor, 0.6);
                    ctx.stroke();

                    // Cache centerline for hit testing.
                    const x1 = (sx0 + sx1) / 2;
                    const x2 = (tx0 + tx1) / 2;
                    const w = Math.max(sx1 - sx0, tx1 - tx0);
                    const control1X = x1;
                    const control1Y = y1 + controlOffset;
                    const control2X = x2;
                    const control2Y = y2 - controlOffset;
                    const minX = Math.min(
                        sx0,
                        sx1,
                        tx0,
                        tx1,
                        control1X,
                        control2X,
                    );
                    const maxX = Math.max(
                        sx0,
                        sx1,
                        tx0,
                        tx1,
                        control1X,
                        control2X,
                    );
                    const minY = Math.min(y1, y2, control1Y, control2Y);
                    const maxY = Math.max(y1, y2, control1Y, control2Y);

                    connectionEntries.push({
                        aggregated: true,
                        count,
                        width: w,
                        color: bandColor,
                        leftSummary: top.summary,
                        rightSummary: bottom.summary,
                        leftCluster: fromCluster,
                        rightCluster: toCluster,
                        points: {
                            x1,
                            y1,
                            cx1: control1X,
                            cy1: control1Y,
                            cx2: control2X,
                            cy2: control2Y,
                            x2,
                            y2,
                        },
                        bounds: { minX, maxX, minY, maxY },
                    });
                }
            } else {
                // Individual links (one per barcode)
                // First pass: count connections per cluster pair to determine line width
                const clusterPairCounts = new Map(); // "fromCluster||toCluster" -> count
                for (const [barcode, topSpots] of top.barcodeMap.entries()) {
                    const bottomSpots = bottom.barcodeMap.get(barcode);
                    if (!bottomSpots?.length) continue;
                    if (isBarcodeFilteredOutByStability(barcode)) continue;

                    const pairCount = Math.min(
                        topSpots.length,
                        bottomSpots.length,
                    );
                    for (let n = 0; n < pairCount; n += 1) {
                        const from = topSpots[n];
                        const to = bottomSpots[n];
                        const fromCluster = String(from.cluster ?? "").trim();
                        const toCluster = String(to.cluster ?? "").trim();
                        if (!fromCluster || !toCluster) continue;
                        const key = `${fromCluster}||${toCluster}`;
                        clusterPairCounts.set(
                            key,
                            (clusterPairCounts.get(key) || 0) + 1,
                        );
                    }
                }

                // Find min and max counts for scaling
                const countsArray = Array.from(clusterPairCounts.values());
                const minCount =
                    countsArray.length > 0 ? Math.min(...countsArray) : 1;
                const maxCount =
                    countsArray.length > 0 ? Math.max(...countsArray) : 1;
                const countRange = Math.max(1, maxCount - minCount);

                // Second pass: draw connections with width based on cluster pair count
                for (const [barcode, topSpots] of top.barcodeMap.entries()) {
                    const bottomSpots = bottom.barcodeMap.get(barcode);
                    if (!bottomSpots?.length) continue;

                    // Filter out barcodes above stability threshold
                    if (isBarcodeFilteredOutByStability(barcode)) continue;

                    const pairCount = Math.min(
                        topSpots.length,
                        bottomSpots.length,
                    );

                    for (let n = 0; n < pairCount; n += 1) {
                        const from = topSpots[n];
                        const to = bottomSpots[n];

                        const fromCluster = String(from.cluster ?? "").trim();
                        const toCluster = String(to.cluster ?? "").trim();
                        const clusterPairKey = `${fromCluster}||${toCluster}`;
                        const pairCountForWidth =
                            clusterPairCounts.get(clusterPairKey) || 1;

                        // Check if this connection should be highlighted (from preview or from Sankey hover)
                        const isHighlighted =
                            (highlightedBarcodeFromPreview &&
                                barcode === highlightedBarcodeFromPreview) ||
                            (highlightedBarcodeFromSankey &&
                                barcode === highlightedBarcodeFromSankey);

                        ctx.beginPath();
                        ctx.moveTo(from.x, y1);
                        ctx.bezierCurveTo(
                            from.x,
                            y1 + controlOffset,
                            to.x,
                            y2 - controlOffset,
                            to.x,
                            y2,
                        );
                        // Apply additional transparency to flow connections
                        // Highlight if this barcode is hovered in preview
                        // Line width scales with cluster pair connection count
                        if (isHighlighted) {
                            ctx.lineWidth = baseLineWidth * 2.5;
                            const flowColor = colorWithAlpha(
                                from.color || to.color,
                                0.85,
                            );
                            ctx.strokeStyle = flowColor;
                        } else {
                            // Scale line width based on cluster pair count
                            const countRatio =
                                countRange > 0
                                    ? (pairCountForWidth - minCount) /
                                      countRange
                                    : 0.5;
                            const lineWidthScale = 0.5 + countRatio * 1.5; // Range: 0.5 to 2.0
                            ctx.lineWidth = Math.max(
                                0.3,
                                baseLineWidth * lineWidthScale,
                            );
                            // If there's a highlight active, make non-highlighted lines gray
                            if (hasHighlight) {
                                // Keep non-highlighted lines visible but de-emphasized
                                ctx.strokeStyle = "rgba(148, 163, 184, 0.35)"; // gray-400 with medium-low opacity
                            } else {
                                const flowColor = colorWithAlpha(
                                    from.color || to.color,
                                    0.6,
                                );
                                ctx.strokeStyle = flowColor;
                            }
                        }
                        ctx.stroke();

                        const control1X = from.x;
                        const control1Y = y1 + controlOffset;
                        const control2X = to.x;
                        const control2Y = y2 - controlOffset;

                        const minX = Math.min(
                            from.x,
                            control1X,
                            control2X,
                            to.x,
                        );
                        const maxX = Math.max(
                            from.x,
                            control1X,
                            control2X,
                            to.x,
                        );
                        const minY = Math.min(y1, control1Y, control2Y, y2);
                        const maxY = Math.max(y1, control1Y, control2Y, y2);

                        connectionEntries.push({
                            barcode,
                            color: from.color || to.color,
                            leftSummary: top.summary,
                            rightSummary: bottom.summary,
                            leftCluster: from.cluster,
                            rightCluster: to.cluster,
                            points: {
                                x1: from.x,
                                y1,
                                cx1: control1X,
                                cy1: control1Y,
                                cx2: control2X,
                                cy2: control2Y,
                                x2: to.x,
                                y2,
                            },
                            bounds: {
                                minX,
                                maxX,
                                minY,
                                maxY,
                            },
                        });
                    }
                }
            }
        }

        ctx.restore();

        ctx.save();
        ctx.globalAlpha = 1;
        ctx.lineWidth = 0.75;
        ctx.strokeStyle = "rgba(71, 85, 105, 0.35)";

        rowLayouts.forEach((layout) => {
            if (!layout.summary.total) {
                ctx.fillStyle = "rgba(148, 163, 184, 0.18)";
                ctx.fillRect(layout.x, layout.y, layout.width, layout.height);
                ctx.fillStyle = "rgba(71, 85, 105, 0.55)";
                ctx.font = "10px/12px var(--font-sans, system-ui, sans-serif)";
                ctx.textAlign = "center";
                ctx.textBaseline = "middle";
                ctx.fillText(
                    "No spots",
                    layout.x + layout.width / 2,
                    layout.y + layout.height / 2,
                );
            } else {
                layout.clusterRects.forEach((rect) => {
                    ctx.fillStyle = rect.color;
                    ctx.fillRect(rect.x, rect.y, rect.width, rect.height);

                    // Draw white divider lines inside cluster bar for trendRects
                    if (rect.trendRects && rect.trendRects.length > 1) {
                        ctx.save();
                        ctx.strokeStyle = "rgba(255, 255, 255, 0.6)";
                        ctx.lineWidth = 1;
                        // Draw divider line at the start of each trend rect (except the first one)
                        rect.trendRects.forEach((trendRect, trendIdx) => {
                            if (trendIdx > 0) {
                                ctx.beginPath();
                                ctx.moveTo(trendRect.x, trendRect.y);
                                ctx.lineTo(
                                    trendRect.x,
                                    trendRect.y + trendRect.height,
                                );
                                ctx.stroke();
                            }
                        });
                        ctx.restore();
                    }

                    // Draw white divider lines inside cluster bar for flowRects (individual mode)
                    if (rect.flowRects && rect.flowRects.length > 1) {
                        ctx.save();
                        ctx.strokeStyle = "rgba(255, 255, 255, 0.5)";
                        ctx.lineWidth = 0.75;
                        ctx.setLineDash([2, 2]); // Dashed line to distinguish from trendRects
                        // Draw divider line at the start of each flow rect (except the first one)
                        rect.flowRects.forEach((flowRect, flowIdx) => {
                            if (flowIdx > 0) {
                                ctx.beginPath();
                                ctx.moveTo(flowRect.x, flowRect.y);
                                ctx.lineTo(
                                    flowRect.x,
                                    flowRect.y + flowRect.height,
                                );
                                ctx.stroke();
                            }
                        });
                        ctx.restore();
                    }
                });
            }

            ctx.strokeRect(layout.x, layout.y, layout.width, layout.height);
        });

        ctx.restore();

        layoutCache = rowLayouts;
        connectionCache = connectionEntries;
        canvasMetrics = { width: drawWidth, height: drawHeight };

        // Update clusterLayoutInfo for alignment with other components
        const newLayoutInfo = new Map();
        rowLayouts.forEach((layout, rowIndex) => {
            const clusterMap = new Map();
            if (layout.clusterRects) {
                layout.clusterRects.forEach((rect) => {
                    const clusterName = String(rect.cluster ?? "").trim();
                    if (clusterName) {
                        clusterMap.set(clusterName, {
                            x: rect.x,
                            width: rect.width,
                        });
                    }
                });
            }
            newLayoutInfo.set(rowIndex, clusterMap);
        });
        clusterLayoutInfo = newLayoutInfo;

        // Update sankey dimensions for alignment
        // After 90° rotation with transform: translate(drawHeight, 0) then rotate(PI/2)
        // Matrix math: for logical (x, y), screen position = (drawHeight - y, x)
        // So: screen x = drawHeight - logical y
        // drawHeight = visibleWidth = screen width after rotation
        // drawWidth = containerHeight = screen height after rotation
        const rowPositions = [];
        for (let ri = 0; ri < rowLayouts.length; ri++) {
            const rowCenterY = firstRowCenterY + ri * rowSpacing;
            // After rotation: screen x = drawHeight - rowCenterY
            const screenX = drawHeight - rowCenterY;
            rowPositions.push(screenX);
        }
        sankeyDimensions = {
            paddingLeft,
            sankeyWidth: sankeyAreaWidth,
            totalWidth: drawWidth,
            screenWidth: drawHeight, // Screen width after rotation
            rowPositions,
            rowHeight,
            rowSpacing,
        };
    }

    function clearCanvas() {
        if (!renderCanvas) return;
        const ctx = renderCanvas.getContext("2d");
        if (!ctx) return;
        ctx.setTransform(1, 0, 0, 1, 0, 0);
        ctx.clearRect(0, 0, renderCanvas.width, renderCanvas.height);
        renderCanvas.style.width = "0px";
        renderCanvas.style.height = "0px";
        layoutCache = [];
        connectionCache = [];
        canvasMetrics = { width: 0, height: 0 };
        clusterLayoutInfo = new Map();
        hoverInfo = null;
        tooltipPosition = { left: 0, top: 0 };
    }

    function handlePointerMove(event) {
        if (!connectionWrapper) return;
        const rect = connectionWrapper.getBoundingClientRect();
        // After rotation: scrollLeft corresponds to original scrollTop
        const scrollX = connectionWrapper.scrollTop; // Swapped for rotation
        const scrollY = connectionWrapper.scrollLeft; // Swapped for rotation
        const screenX = event.clientX - rect.left + scrollY;
        const screenY = event.clientY - rect.top + scrollX;
        // Transform screen coordinates to logical drawing coordinates
        const { x, y } = screenToLogical(screenX, screenY);

        // Check metrics first (since they're on the right, higher priority)
        // Metrics are not in Sankey view anymore, so skip
        // const metricsHit = hitTestMetrics(x, y);
        // if (metricsHit) {
        //     hoverInfo = metricsHit;
        //     updateTooltipPosition(event.clientX, event.clientY);
        //     return;
        // }

        const clusterHit = hitTestCluster(x, y);
        if (clusterHit) {
            hoverInfo = clusterHit;
            updateTooltipPosition(event.clientX, event.clientY);
            // Clear single barcode highlight from connection hover
            highlightedBarcodeFromSankey = null;

            // Dispatch event to parent component for highlighting
            // For flow/trend/cluster hover, send barcodes to parent for highlighting
            if (clusterHit.type === "flow" && clusterHit.summary?.spots) {
                const fromCluster = String(clusterHit.fromCluster).trim();
                const toCluster = String(clusterHit.toCluster).trim();
                const flowBarcodes = [];

                const rv = rowVisibleCache?.[clusterHit.layoutIndex ?? 0];
                const spotsForRow =
                    rv?.visibleSpots || clusterHit.summary.spots || [];
                const nextMap =
                    clusterHit.layoutIndex !== undefined &&
                    clusterHit.layoutIndex < rowVisibleCache.length - 1
                        ? rowVisibleCache[clusterHit.layoutIndex + 1]
                              ?.barcodeClusterMap
                        : null;

                spotsForRow.forEach((spot) => {
                    const barcode = `${spot.barcode}`.trim();
                    if (!barcode || isBarcodeFilteredOutByStability(barcode))
                        return;
                    if (String(spot.cluster).trim() !== fromCluster) return;
                    if (nextMap) {
                        const nextCluster = nextMap.get(barcode) || "";
                        if (String(nextCluster).trim() === toCluster) {
                            flowBarcodes.push(barcode);
                        }
                    }
                });

                dispatch("highlightBarcodes", {
                    barcodes: flowBarcodes,
                    color: colorWithAlpha(
                        clusterHit.color || "rgba(234, 88, 12, 1)",
                        0.9,
                    ),
                    type: "flow",
                });
            } else if (
                clusterHit.type === "trend" &&
                clusterHit.summary?.spots
            ) {
                const clusterName = String(clusterHit.cluster).trim();
                const trendKey = clusterHit.trendKey;
                const trendBarcodes = [];

                const rv = rowVisibleCache?.[clusterHit.layoutIndex ?? 0];
                const spotsForRow =
                    rv?.visibleSpots || clusterHit.summary.spots || [];
                spotsForRow.forEach((spot) => {
                    const barcode = `${spot.barcode}`.trim();
                    if (!barcode || isBarcodeFilteredOutByStability(barcode))
                        return;
                    if (String(spot.cluster).trim() !== clusterName) return;

                    if (trendKey === ADJ_STABLE_KEY) {
                        const li = Number.isFinite(clusterHit.layoutIndex)
                            ? clusterHit.layoutIndex
                            : null;
                        if (li === null) return;
                        if (
                            isAdjacentStableAtRow(
                                rowVisibleCache,
                                li,
                                barcode,
                                clusterName,
                            )
                        ) {
                            trendBarcodes.push(barcode);
                        }
                        return;
                    }

                    const matchKey =
                        segmentMode === "next_cluster"
                            ? (clusterHit.layoutIndex ?? 0) ===
                              rowVisibleCache.length - 1
                                ? getPrevClusterKey(
                                      rowVisibleCache,
                                      clusterHit.layoutIndex ?? 0,
                                      barcode,
                                  )
                                : getNextClusterKey(
                                      rowVisibleCache,
                                      clusterHit.layoutIndex ?? 0,
                                      barcode,
                                  )
                            : segmentMode === "cluster_set"
                              ? spot.clusterSetKey
                              : spot.trendKey;
                    if (matchKey === trendKey) {
                        trendBarcodes.push(barcode);
                    }
                });

                dispatch("highlightBarcodes", {
                    barcodes: trendBarcodes,
                    color: colorWithAlpha(
                        clusterHit.color || "rgba(234, 88, 12, 1)",
                        0.9,
                    ),
                    type: "trend",
                });
            } else if (clusterHit.summary?.spots) {
                const clusterName = String(clusterHit.cluster).trim();
                const rv = rowVisibleCache?.[clusterHit.layoutIndex ?? 0];
                const spotsForRow =
                    rv?.visibleSpots || clusterHit.summary.spots || [];
                const clusterBarcodes = spotsForRow
                    .filter(
                        (spot) => String(spot.cluster).trim() === clusterName,
                    )
                    .map((spot) => spot.barcode)
                    .filter((bc) => {
                        if (bc === null || bc === undefined) return false;
                        const barcode = `${bc}`.trim();
                        if (!barcode) return false;
                        return !isBarcodeFilteredOutByStability(barcode);
                    });

                dispatch("highlightBarcodes", {
                    barcodes: clusterBarcodes,
                    color: colorWithAlpha(
                        clusterHit.color || "rgba(234, 88, 12, 1)",
                        0.9,
                    ),
                    type: "cluster",
                });
            } else {
                dispatch("highlightBarcodes", {
                    barcodes: [],
                    color: null,
                    type: null,
                });
            }
            return;
        }

        const connectionHit = hitTestConnection(x, y);
        if (connectionHit) {
            hoverInfo = connectionHit;
            updateTooltipPosition(event.clientX, event.clientY);

            // Aggregated connection hover: highlight the set of involved barcodes (capped) and show count tooltip.
            if (connectionHit.aggregated) {
                // Clear any single-barcode highlight/pie behavior
                if (highlightedBarcodeFromSankey !== null) {
                    highlightedBarcodeFromSankey = null;
                    dispatch("highlightBarcode", { barcode: null });
                }

                const leftCluster = String(
                    connectionHit.leftCluster ?? "",
                ).trim();
                const rightCluster = String(
                    connectionHit.rightCluster ?? "",
                ).trim();
                const leftSummary = connectionHit.leftSummary;
                const rightSummary = connectionHit.rightSummary;
                if (
                    leftSummary &&
                    rightSummary &&
                    leftCluster &&
                    rightCluster
                ) {
                    const rightMap = new Map();
                    (rightSummary.spots || []).forEach((s) => {
                        const bc =
                            s?.barcode === null || s?.barcode === undefined
                                ? ""
                                : `${s.barcode}`.trim();
                        if (!bc || isBarcodeFilteredOutByStability(bc)) return;
                        rightMap.set(bc, String(s.cluster).trim());
                    });

                    const barcodes = [];
                    (leftSummary.spots || []).forEach((s) => {
                        const bc =
                            s?.barcode === null || s?.barcode === undefined
                                ? ""
                                : `${s.barcode}`.trim();
                        if (!bc || isBarcodeFilteredOutByStability(bc)) return;
                        if (String(s.cluster).trim() !== leftCluster) return;
                        const toC = rightMap.get(bc);
                        if (String(toC ?? "").trim() === rightCluster) {
                            barcodes.push(bc);
                        }
                    });

                    const highlightColor = colorWithAlpha(
                        connectionHit.color || "rgba(148, 163, 184, 1)",
                        0.9,
                    );
                    dispatch("highlightBarcodes", {
                        barcodes: barcodes.slice(0, 600),
                        color: highlightColor,
                        type: "connection",
                    });
                }
            } else {
                // Individual connection hover uses single-spot pie.
                if (connectionHit.barcode) {
                    const bcStr = `${connectionHit.barcode}`.trim();
                    // Only update if barcode changed
                    if (highlightedBarcodeFromSankey !== bcStr) {
                        highlightedBarcodeFromSankey = bcStr;
                        dispatch("highlightBarcode", { barcode: bcStr });
                        // Redraw to highlight the connection line
                        scheduleMeasure();
                    }
                } else {
                    if (highlightedBarcodeFromSankey !== null) {
                        highlightedBarcodeFromSankey = null;
                        dispatch("highlightBarcode", { barcode: null });
                        scheduleMeasure();
                    }
                }
            }
        } else {
            hoverInfo = null;
            if (highlightedBarcodeFromSankey !== null) {
                highlightedBarcodeFromSankey = null;
                dispatch("highlightBarcode", { barcode: null });
                scheduleMeasure();
            } else {
                // Clear cluster highlight if was hovering on cluster
                dispatch("highlightBarcodes", {
                    barcodes: [],
                    color: null,
                    type: null,
                });
            }
        }
    }

    function handlePointerLeave() {
        hoverInfo = null;
        highlightedBarcodeFromSankey = null;
        scheduleMeasure();
    }

    function handleSankeyClick(event) {
        if (!connectionWrapper) return;

        // Clear any existing click timer
        if (clickTimer) {
            clearTimeout(clickTimer);
            clickTimer = null;
        }

        const rect = connectionWrapper.getBoundingClientRect();
        // After rotation: scrollLeft corresponds to original scrollTop
        const scrollX = connectionWrapper.scrollTop; // Swapped for rotation
        const scrollY = connectionWrapper.scrollLeft; // Swapped for rotation
        const screenX = event.clientX - rect.left + scrollY;
        const screenY = event.clientY - rect.top + scrollX;
        // Transform screen coordinates to logical drawing coordinates
        const { x, y } = screenToLogical(screenX, screenY);

        const clusterHit = hitTestCluster(x, y);
        if (!clusterHit) return;

        // Single click on cluster bar: expand/focus that cluster in this row only
        const rowIndex = Number.isFinite(clusterHit.layoutIndex)
            ? clusterHit.layoutIndex
            : null;
        if (rowIndex === null) return;
        const clusterName = String(clusterHit.cluster ?? "").trim();
        if (!clusterName) return;

        // Delay single-click action to allow double-click to cancel it
        clickTimer = setTimeout(() => {
            clickTimer = null;
            
            // Toggle expansion for this row only (collapse other rows first)
            const currentFocus = focusedClusterByRow?.[rowIndex];
            let next = {};

            if (currentFocus === clusterName) {
                // Clicking the same cluster: collapse it (remove from focusedClusterByRow)
                // next is already empty, so all rows are collapsed
                activeFocus = null;
                activeFocusRank = null;
            } else {
                // Expand the clicked cluster for this row (clear other rows first)
                next[rowIndex] = clusterName;
                activeFocus = { rowIndex, clusterName };
                // Freeze the focused cluster's barcode order
                const rank = new Map();
                const rv = rowVisibleCache?.[rowIndex];
                const spotsInRow =
                    rv?.visibleSpots ||
                    visibleDistributionSummary?.[rowIndex]?.spots ||
                    [];
                let i = 0;
                for (const s of spotsInRow) {
                    const bc =
                        s?.barcode === null || s?.barcode === undefined
                            ? ""
                            : `${s.barcode}`.trim();
                    if (!bc || isBarcodeFilteredOutByStability(bc)) continue;
                    if (String(s?.cluster ?? "").trim() !== clusterName) continue;
                    if (!rank.has(bc)) {
                        rank.set(bc, i);
                        i += 1;
                    }
                }
                activeFocusRank = rank.size > 0 ? rank : null;
            }

            // Determine flowMode based on whether any clusters are expanded
            const hasExpansion = Object.keys(next).length > 0;

            // Dispatch event to parent to update focusedClusterByRow
            dispatch("clusterExpand", {
                flowMode: hasExpansion ? "individual" : "aggregated",
                focusedClusterByRow: next,
                activeFocus,
                activeFocusRank,
            });
            focusedClusterByRow = next;
            scheduleMeasure();
        }, 200); // 200ms delay to detect double-click
    }

    function handleSankeyDoubleClick(event) {
        // Cancel any pending single-click action
        if (clickTimer) {
            clearTimeout(clickTimer);
            clickTimer = null;
        }
        
        if (!connectionWrapper) return;
        const rect = connectionWrapper.getBoundingClientRect();
        // After rotation: scrollLeft corresponds to original scrollTop
        const scrollX = connectionWrapper.scrollTop; // Swapped for rotation
        const scrollY = connectionWrapper.scrollLeft; // Swapped for rotation
        const screenX = event.clientX - rect.left + scrollY;
        const screenY = event.clientY - rect.top + scrollX;
        // Transform screen coordinates to logical drawing coordinates
        const { x, y } = screenToLogical(screenX, screenY);

        const clusterHit = hitTestCluster(x, y);
        if (!clusterHit) return;

        // In individual mode: double-click on flow segment navigates to lasso
        if (flowMode === "individual" && clusterHit.type === "flow") {
            const layoutIndex = clusterHit.layoutIndex;
            if (
                !Number.isFinite(layoutIndex) ||
                layoutIndex < 0 ||
                layoutIndex >= visibleDistributionSummary.length
            )
                return;

            const summary = visibleDistributionSummary[layoutIndex];
            const clusterResultId = summary?.result?.cluster_result_id;
            if (!clusterResultId) return;

            const fromCluster = String(clusterHit.fromCluster ?? "").trim();
            const toCluster = String(clusterHit.toCluster ?? "").trim();
            if (!fromCluster || !toCluster) return;

            // Get all barcodes in this flow segment
            const rv = rowVisibleCache?.[layoutIndex];
            const spotsForRow = rv?.visibleSpots || summary.spots || [];
            const nextMap =
                layoutIndex < rowVisibleCache.length - 1
                    ? rowVisibleCache[layoutIndex + 1]?.barcodeClusterMap
                    : null;

            const flowBarcodes = [];
            spotsForRow.forEach((spot) => {
                const barcode = `${spot.barcode}`.trim();
                if (!barcode || isBarcodeFilteredOutByStability(barcode))
                    return;
                if (String(spot.cluster).trim() !== fromCluster) return;
                if (nextMap) {
                    const nextCluster = nextMap.get(barcode) || "";
                    if (String(nextCluster).trim() === toCluster) {
                        flowBarcodes.push(barcode);
                    }
                }
            });

            if (flowBarcodes.length === 0) return;

            // Dispatch event to parent component to navigate to lasso mode
            dispatch("flowSegmentClick", {
                clusterResultId,
                barcodes: flowBarcodes,
                fromCluster,
                toCluster,
                result: summary.result,
                count: flowBarcodes.length,
            });
            return;
        }

        // Double-click on cluster bar or trend segment: navigate to detail view for that result
        const layoutIndex = clusterHit.layoutIndex;
        if (
            Number.isFinite(layoutIndex) &&
            layoutIndex >= 0 &&
            layoutIndex < visibleDistributionSummary.length
        ) {
            const summary = visibleDistributionSummary[layoutIndex];
            if (summary?.result) {
                const clickedCluster = String(clusterHit.cluster ?? "").trim();
                const rv = rowVisibleCache?.[layoutIndex];
                const spotsForRow = rv?.visibleSpots || summary.spots || [];
                
                // Collect barcodes based on hit type
                const segmentBarcodes = [];
                
                if (clusterHit.type === "trend" && clusterHit.trendKey) {
                    // Clicking on a trend segment: select only spots in that segment
                    // Use the same logic as hover highlighting for consistency
                    const trendKey = clusterHit.trendKey;
                    
                    spotsForRow.forEach((spot) => {
                        const barcode = `${spot.barcode}`.trim();
                        if (!barcode || isBarcodeFilteredOutByStability(barcode)) return;
                        // Must be in the same cluster
                        if (String(spot.cluster).trim() !== clickedCluster) return;
                        
                        // Handle adjacent stable group
                        if (trendKey === ADJ_STABLE_KEY) {
                            if (isAdjacentStableAtRow(rowVisibleCache, layoutIndex, barcode, clickedCluster)) {
                                segmentBarcodes.push(barcode);
                            }
                            return;
                        }
                        
                        // Match by segment mode - same logic as hover highlighting
                        const matchKey =
                            segmentMode === "next_cluster"
                                ? layoutIndex === rowVisibleCache.length - 1
                                    ? getPrevClusterKey(rowVisibleCache, layoutIndex, barcode)
                                    : getNextClusterKey(rowVisibleCache, layoutIndex, barcode)
                                : segmentMode === "cluster_set"
                                  ? spot.clusterSetKey
                                  : spot.trendKey;
                        if (matchKey === trendKey) {
                            segmentBarcodes.push(barcode);
                        }
                    });
                } else if (clusterHit.type === "flow" && clusterHit.fromCluster && clusterHit.toCluster) {
                    // Clicking on a flow segment (aggregated mode): select spots in that flow
                    const fromCluster = String(clusterHit.fromCluster).trim();
                    const toCluster = String(clusterHit.toCluster).trim();
                    const nextMap = layoutIndex < (rowVisibleCache?.length || 0) - 1
                        ? rowVisibleCache[layoutIndex + 1]?.barcodeClusterMap
                        : null;
                    
                    spotsForRow.forEach((spot) => {
                        const barcode = `${spot.barcode}`.trim();
                        if (!barcode || isBarcodeFilteredOutByStability(barcode)) return;
                        if (String(spot.cluster).trim() !== fromCluster) return;
                        if (nextMap) {
                            const nextCluster = nextMap.get(barcode) || "";
                            if (String(nextCluster).trim() === toCluster) {
                                segmentBarcodes.push(barcode);
                            }
                        }
                    });
                } else {
                    // Clicking on a cluster bar: select all spots in that cluster
                    spotsForRow.forEach((spot) => {
                        const barcode = `${spot.barcode}`.trim();
                        if (!barcode || isBarcodeFilteredOutByStability(barcode)) return;
                        if (clickedCluster) {
                            if (String(spot.cluster).trim() === clickedCluster) {
                                segmentBarcodes.push(barcode);
                            }
                        } else {
                            segmentBarcodes.push(barcode);
                        }
                    });
                }
                
                // Dispatch event to navigate to detail view and select this result
                dispatch("selectClusterResult", {
                    result: summary.result,
                    cluster_result_id: summary.result.cluster_result_id,
                    method: summary.result.method,
                    n_clusters: summary.result.n_clusters,
                    epoch: summary.result.epoch,
                    autoSelected: false,
                    isPreview: false, // Navigate to detail view, not preview
                    barcodes: segmentBarcodes,
                    cluster: clickedCluster,
                });
                return;
            }
        }

        // Fallback: collapse all and go back to aggregated mode
        activeFocus = null;
        activeFocusRank = null;
        focusedClusterByRow = {};
        dispatch("clusterExpand", {
            flowMode: "aggregated",
            focusedClusterByRow: {},
            activeFocus: null,
            activeFocusRank: null,
        });
        scheduleMeasure();
    }

    function updateTooltipPosition(clientX, clientY) {
        const margin = 12;
        // 根据hoverInfo类型动态调整tooltip大小
        let tooltipWidth = 200;
        let tooltipHeight = 96;
        if (hoverInfo?.type === "connection") {
            if (hoverInfo?.aggregated) {
                tooltipWidth = 260;
                tooltipHeight = 120;
            } else {
                // connection类型需要显示所有算法的簇信息，需要更大的宽度和高度
                tooltipWidth = 280;
                tooltipHeight = Math.min(
                    300,
                    80 + (visibleDistributionSummary?.length || 0) * 18,
                );
            }
        } else if (hoverInfo?.type === "trend" || hoverInfo?.type === "flow") {
            // trend和flow类型需要显示更多信息，需要更大的宽度和高度
            tooltipWidth = 240;
            tooltipHeight =
                140 + (visibleDistributionSummary?.length || 0) * 12;
        }

        // 使用viewport尺寸
        const viewportWidth =
            typeof window !== "undefined" ? window.innerWidth : 0;
        const viewportHeight =
            typeof window !== "undefined" ? window.innerHeight : 0;

        let left = clientX + margin;
        let top = clientY - margin;

        // 如果tooltip超出右边界，放在左边
        if (left + tooltipWidth > viewportWidth) {
            left = Math.max(margin, clientX - tooltipWidth - margin);
        }
        // 如果tooltip超出上边界，放在下面
        if (top < margin) {
            top = Math.min(
                viewportHeight - tooltipHeight - margin,
                clientY + margin,
            );
        }
        // 确保不超出下边界
        if (top + tooltipHeight > viewportHeight) {
            top = Math.max(margin, viewportHeight - tooltipHeight - margin);
        }
        // 确保不超出左边界
        if (left < margin) {
            left = margin;
        }

        tooltipPosition = {
            left: Math.max(margin, left),
            top: Math.max(margin, top),
        };
    }

    function cubicAt(p0, p1, p2, p3, t) {
        const mt = 1 - t;
        return (
            mt * mt * mt * p0 +
            3 * mt * mt * t * p1 +
            3 * mt * t * t * p2 +
            t * t * t * p3
        );
    }

    function approxDistanceToCurve(points, x, y) {
        const samples = 24;
        let best = Infinity;
        for (let i = 0; i <= samples; i += 1) {
            const t = i / samples;
            const px = cubicAt(points.x1, points.cx1, points.cx2, points.x2, t);
            const py = cubicAt(points.y1, points.cy1, points.cy2, points.y2, t);
            const dx = px - x;
            const dy = py - y;
            const distSq = dx * dx + dy * dy;
            if (distSq < best) {
                best = distSq;
            }
        }
        return Math.sqrt(best);
    }

    function hitTestCluster(x, y) {
        for (
            let layoutIndex = 0;
            layoutIndex < layoutCache.length;
            layoutIndex += 1
        ) {
            const layout = layoutCache[layoutIndex];
            if (!layout?.clusterRects?.length) continue;
            for (const rect of layout.clusterRects) {
                if (
                    x >= rect.x &&
                    x <= rect.x + rect.width &&
                    y >= rect.y &&
                    y <= rect.y + rect.height
                ) {
                    // In individual mode, check if hovering over a flow rect first
                    if (
                        flowMode === "individual" &&
                        rect.flowRects &&
                        rect.flowRects.length > 0
                    ) {
                        for (const flowRect of rect.flowRects) {
                            if (
                                x >= flowRect.x &&
                                x <= flowRect.x + flowRect.width &&
                                y >= flowRect.y &&
                                y <= flowRect.y + flowRect.height
                            ) {
                                return {
                                    type: "flow",
                                    layoutIndex,
                                    summary: layout.summary,
                                    cluster: rect.cluster,
                                    color: rect.color,
                                    fromCluster: flowRect.fromCluster,
                                    toCluster: flowRect.toCluster,
                                    count: flowRect.count,
                                    percent: flowRect.percent,
                                    clusterCount: rect.count,
                                    clusterPercent: rect.percent,
                                    total: layout.total,
                                };
                            }
                        }
                    }

                    // Check if hovering over a trend rect
                    if (rect.trendRects && rect.trendRects.length > 0) {
                        for (const trendRect of rect.trendRects) {
                            if (
                                x >= trendRect.x &&
                                x <= trendRect.x + trendRect.width &&
                                y >= trendRect.y &&
                                y <= trendRect.y + trendRect.height
                            ) {
                                return {
                                    type: "trend",
                                    layoutIndex,
                                    summary: layout.summary,
                                    cluster: rect.cluster,
                                    color: rect.color,
                                    trendKey: trendRect.trendKey,
                                    trendSequence: trendRect.trendSequence,
                                    count: trendRect.count,
                                    percent: trendRect.percent,
                                    clusterCount: rect.count,
                                    clusterPercent: rect.percent,
                                    total: layout.total,
                                    isAdjStableGroup:
                                        !!trendRect.isAdjStableGroup,
                                };
                            }
                        }
                    }

                    // Fall back to cluster hover
                    return {
                        type: "cluster",
                        layoutIndex,
                        summary: layout.summary,
                        cluster: rect.cluster,
                        color: rect.color,
                        count: rect.count,
                        percent: rect.percent,
                        total: layout.total,
                    };
                }
            }
        }
        return null;
    }

    function hitTestConnection(x, y) {
        let best = null;
        let bestDist = Infinity;
        const baseThreshold = 6;

        for (const entry of connectionCache) {
            const threshold =
                entry &&
                typeof entry.width === "number" &&
                Number.isFinite(entry.width)
                    ? Math.max(baseThreshold, entry.width / 2 + 2)
                    : baseThreshold;
            const { bounds } = entry;
            if (
                x < bounds.minX - threshold ||
                x > bounds.maxX + threshold ||
                y < bounds.minY - threshold ||
                y > bounds.maxY + threshold
            ) {
                continue;
            }
            const dist = approxDistanceToCurve(entry.points, x, y);
            if (dist < threshold && dist < bestDist) {
                best = entry;
                bestDist = dist;
            }
        }

        return best
            ? {
                  type: "connection",
                  distance: bestDist,
                  ...best,
              }
            : null;
    }

    // Reactive statements
    $: if (
        visibleDistributionSummary?.length &&
        connectionWrapper &&
        renderCanvas
    ) {
        scheduleMeasure();
    } else if (!visibleDistributionSummary?.length) {
        layoutCache = [];
        clearCanvas();
    }

    // 用独立变量跟踪 maxStabilityPercent 变化，确保稳定性筛选改变时重新计算排序
    let prevMaxStabilityPercentForSankey = maxStabilityPercent;
    $: if (maxStabilityPercent !== prevMaxStabilityPercentForSankey) {
        prevMaxStabilityPercentForSankey = maxStabilityPercent;
        if (visibleDistributionSummary?.length && connectionWrapper && renderCanvas) {
            scheduleMeasure();
        }
    }

    $: if (
        segmentMode &&
        visibleDistributionSummary?.length &&
        connectionWrapper &&
        renderCanvas
    ) {
        scheduleMeasure();
    }

    $: if (
        flowMode &&
        visibleDistributionSummary?.length &&
        connectionWrapper &&
        renderCanvas
    ) {
        scheduleMeasure();
    }

    // Setup scroll sync
    function teardownScrollSync() {
        detachScrollSync();
        detachScrollSync = () => {};
        activeScrollSource = null;
    }

    function setupScrollSync() {
        if (!connectionWrapper || !scrollSource) {
            teardownScrollSync();
            return;
        }

        if (activeScrollSource === scrollSource) {
            return;
        }

        teardownScrollSync();

        const source = scrollSource;
        const target = connectionWrapper;
        let syncing = false;

        const syncScroll = (from, to) => {
            if (!to) return;
            // After 90° rotation: scrollLeft <-> scrollTop are swapped
            if (verticalLayout) {
                // Rotated layout: sync horizontal scroll (was vertical before rotation)
                const value = from.scrollLeft;
                if (Math.abs(to.scrollLeft - value) > 1) {
                    to.scrollLeft = value;
                }
            } else {
                const value = from.scrollTop;
                if (Math.abs(to.scrollTop - value) > 1) {
                    to.scrollTop = value;
                }
            }
        };

        const handleSourceScroll = () => {
            if (syncing) return;
            syncing = true;
            syncScroll(source, target);
            requestAnimationFrame(() => {
                syncing = false;
            });
        };

        const handleTargetScroll = () => {
            if (syncing) return;
            syncing = true;
            syncScroll(target, source);
            requestAnimationFrame(() => {
                syncing = false;
            });
        };

        source.addEventListener("scroll", handleSourceScroll, {
            passive: true,
        });
        target.addEventListener("scroll", handleTargetScroll, {
            passive: true,
        });

        detachScrollSync = () => {
            source.removeEventListener("scroll", handleSourceScroll);
            target.removeEventListener("scroll", handleTargetScroll);
        };

        activeScrollSource = scrollSource;
    }

    $: {
        if (connectionWrapper && scrollSource) {
            setupScrollSync();
        } else {
            teardownScrollSync();
        }
    }

    $: if (connectionWrapper && typeof ResizeObserver !== "undefined") {
        if (resizeObserver) {
            resizeObserver.disconnect();
        }
        resizeObserver = new ResizeObserver((entries) => {
            for (const entry of entries) {
                if (entry.target === connectionWrapper) {
                    scheduleMeasure();
                }
            }
        });
        resizeObserver.observe(connectionWrapper);
    }

    onMount(() => {
        const handleResize = () => scheduleMeasure();
        window.addEventListener("resize", handleResize);
        scheduleMeasure();

        if (connectionWrapper && typeof ResizeObserver !== "undefined") {
            if (resizeObserver) {
                resizeObserver.disconnect();
            }
            resizeObserver = new ResizeObserver((entries) => {
                for (const entry of entries) {
                    if (entry.target === connectionWrapper) {
                        scheduleMeasure();
                    }
                }
            });
            resizeObserver.observe(connectionWrapper);
        }

        return () => {
            window.removeEventListener("resize", handleResize);
            if (measureRaf) window.cancelAnimationFrame(measureRaf);
            if (resizeObserver) {
                resizeObserver.disconnect();
                resizeObserver = null;
            }
        };
    });

    onDestroy(() => {
        teardownScrollSync();
        if (resizeObserver) {
            resizeObserver.disconnect();
            resizeObserver = null;
        }
    });

    // Expose highlightedBarcodeFromSankey to parent
    $: if (highlightedBarcodeFromSankey !== null) {
        dispatch("highlightedBarcodeChange", {
            barcode: highlightedBarcodeFromSankey,
        });
    } else {
        dispatch("highlightedBarcodeChange", { barcode: null });
    }
</script>

<div
    class="relative flex flex-1 min-h-0 min-w-0 overflow-hidden"
    style="flex: 1 1 0; min-height: 0;"
>
    <!-- svelte-ignore a11y_no_noninteractive_element_interactions a11y_no_noninteractive_tabindex -->
    <div
        class="relative flex-1 min-w-0 min-h-0 {verticalLayout
            ? 'overflow-x-auto overflow-y-hidden'
            : 'overflow-y-auto overflow-x-hidden'}"
        bind:this={connectionWrapper}
        on:pointermove={handlePointerMove}
        on:pointerleave={handlePointerLeave}
        on:dblclick={handleSankeyDoubleClick}
        on:click={handleSankeyClick}
        on:keydown={(e) => {
            if (e.key === "Enter" || e.key === " ") handleSankeyClick(e);
        }}
        role="application"
        tabindex="0"
        style="flex: 1 1 0; min-height: 0; position: relative;"
    >
        <canvas
            bind:this={renderCanvas}
            class="block"
            style="display: block; flex-shrink: 0;"
        ></canvas>
    </div>
</div>

<!-- Tooltip -->
{#if hoverInfo}
    <div
        class="pointer-events-none fixed z-50 rounded-md bg-slate-900/95 px-2 py-2 text-[11px] text-white shadow-lg backdrop-blur-sm {hoverInfo.type ===
        'connection'
            ? 'max-w-[300px]'
            : hoverInfo.type === 'trend' || hoverInfo.type === 'flow'
              ? 'max-w-[260px]'
              : 'max-w-[220px]'}"
        style={`left: ${Math.round(tooltipPosition.left)}px; top: ${Math.round(tooltipPosition.top)}px;`}
    >
        {#if hoverInfo.type === "cluster"}
            <div class="text-[12px] font-semibold">
                {getResultLabel(hoverInfo.summary)}
            </div>
            <div class="mt-1 flex items-center gap-1">
                <span
                    class="inline-flex h-2 w-2 rounded-full"
                    style={`background:${hoverInfo.color};`}
                ></span>
                <span class="font-medium">{hoverInfo.cluster}</span>
            </div>
            <div class="mt-1 text-[10px] text-slate-200">
                {hoverInfo.count} spots ({hoverInfo.percent?.toFixed(1) ??
                    ((hoverInfo.count / (hoverInfo.total || 1)) * 100).toFixed(
                        1,
                    )}%)
            </div>
        {:else if hoverInfo.type === "flow"}
            <div class="text-[12px] font-semibold">
                {getResultLabel(hoverInfo.summary)}
            </div>
            <div class="mt-1 flex items-center gap-1">
                <span
                    class="inline-flex h-2 w-2 rounded-full"
                    style={`background:${hoverInfo.color};`}
                ></span>
                <span class="font-medium">{hoverInfo.fromCluster}</span>
                <span class="text-slate-400">→</span>
                <span class="font-medium">{hoverInfo.toCluster}</span>
            </div>
            <div class="mt-1 text-[10px] text-slate-300 font-medium">
                Flow segment
            </div>
            <div class="mt-1 text-[10px] text-slate-200">
                {hoverInfo.count} spots ({hoverInfo.percent?.toFixed(1)}%)
            </div>
        {:else if hoverInfo.type === "trend"}
            <div class="text-[12px] font-semibold">
                {getResultLabel(hoverInfo.summary)}
            </div>
            <div class="mt-1 flex items-center gap-1">
                <span
                    class="inline-flex h-2 w-2 rounded-full"
                    style={`background:${hoverInfo.color};`}
                ></span>
                <span class="font-medium">{hoverInfo.cluster}</span>
            </div>
            <div class="mt-1 text-[10px] text-slate-300 font-medium">
                {hoverInfo.isAdjStableGroup
                    ? "Adjacent stable"
                    : segmentMode === "cluster_only"
                      ? "Cluster only"
                      : segmentMode === "next_cluster"
                        ? "Next cluster"
                        : segmentMode === "cluster_set"
                          ? "Cluster Set"
                          : "Trend Pattern"}
            </div>
            <div class="mt-1 text-[10px] text-slate-200">
                {hoverInfo.count} spots ({hoverInfo.percent?.toFixed(1)}%)
            </div>
            <div class="mt-1.5 pt-1 border-t border-slate-700">
                <div class="text-[10px] text-slate-300 mb-0.5">
                    {hoverInfo.isAdjStableGroup
                        ? "Centered at:"
                        : segmentMode === "next_cluster"
                          ? "From → To:"
                          : segmentMode === "cluster_set"
                            ? "Clusters involved:"
                            : "Cluster sequence:"}
                </div>
                <div class="flex flex-wrap gap-1">
                    {#if hoverInfo.isAdjStableGroup}
                        <span
                            class="text-[9px] px-1 py-0.5 rounded bg-slate-700 text-slate-200"
                        >
                            {hoverInfo.cluster}
                        </span>
                    {:else if segmentMode === "next_cluster"}
                        {#if hoverInfo.layoutIndex === visibleDistributionSummary.length - 1}
                            <span
                                class="text-[9px] px-1 py-0.5 rounded bg-slate-700 text-slate-200"
                            >
                                {hoverInfo.trendKey}
                            </span>
                            <span class="text-[9px] text-slate-400">→</span>
                            <span
                                class="text-[9px] px-1 py-0.5 rounded bg-slate-700 text-slate-200"
                            >
                                {hoverInfo.cluster}
                            </span>
                        {:else}
                            <span
                                class="text-[9px] px-1 py-0.5 rounded bg-slate-700 text-slate-200"
                            >
                                {hoverInfo.cluster}
                            </span>
                            <span class="text-[9px] text-slate-400">→</span>
                            <span
                                class="text-[9px] px-1 py-0.5 rounded bg-slate-700 text-slate-200"
                            >
                                {hoverInfo.trendKey}
                            </span>
                        {/if}
                    {:else if segmentMode === "cluster_set"}
                        {#each hoverInfo.trendSequence as c}
                            <span
                                class="text-[9px] px-1 py-0.5 rounded bg-slate-700 text-slate-200"
                            >
                                {c}
                            </span>
                        {/each}
                    {:else}
                        {#each visibleDistributionSummary as summary, idx}
                            {@const clusterInSeq = hoverInfo.trendSequence[idx]}
                            {#if clusterInSeq !== null && clusterInSeq !== undefined}
                                <span
                                    class="text-[9px] px-1 py-0.5 rounded bg-slate-700 text-slate-200"
                                >
                                    {clusterInSeq}
                                </span>
                            {:else}
                                <span
                                    class="text-[9px] px-1 py-0.5 rounded bg-slate-800 text-slate-400"
                                >
                                    ?
                                </span>
                            {/if}
                        {/each}
                    {/if}
                </div>
            </div>
        {:else if hoverInfo.type === "connection"}
            {#if hoverInfo.aggregated}
                <div class="text-[12px] font-semibold">Aggregated flow</div>
                <div class="mt-1 text-[10px] text-slate-200">
                    {getResultLabel(hoverInfo.leftSummary)} → {getResultLabel(
                        hoverInfo.rightSummary,
                    )}
                </div>
                <div
                    class="mt-1 flex items-center gap-1 text-[10px] text-slate-200"
                >
                    <span
                        class="inline-flex h-2 w-2 rounded-full"
                        style={`background:${hoverInfo.color};`}
                    ></span>
                    <span class="font-medium">{hoverInfo.leftCluster}</span>
                    <span class="text-slate-400">→</span>
                    <span class="font-medium">{hoverInfo.rightCluster}</span>
                </div>
                <div class="mt-1 text-[10px] text-slate-200">
                    <span class="font-medium">Count:</span>
                    {hoverInfo.count}
                </div>
            {:else}
                <div class="text-[12px] font-semibold">
                    {hoverInfo.barcode}
                </div>
                <div
                    class="mt-1 flex flex-col gap-0.5 text-[10px] text-slate-200 max-h-[200px] overflow-y-auto"
                >
                    {#each visibleDistributionSummary as summary}
                        {@const barcode = hoverInfo.barcode}
                        {@const spot = summary.spots?.find(
                            (s) =>
                                `${s.barcode}`.trim() === `${barcode}`.trim(),
                        )}
                        {#if spot}
                            <div class="flex items-center gap-1">
                                <span
                                    class="inline-flex h-1.5 w-1.5 rounded-full flex-shrink-0"
                                    style={`background:${spot.color || summary.clusters?.find((c) => `${c.cluster}`.trim() === `${spot.cluster}`.trim())?.color || "#999999"};`}
                                ></span>
                                <span class="truncate">
                                    {getResultLabel(summary)} · {spot.cluster ||
                                        "—"}
                                </span>
                            </div>
                        {/if}
                    {/each}
                </div>
            {/if}
        {/if}
    </div>
{/if}
