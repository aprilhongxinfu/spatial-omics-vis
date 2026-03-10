import os
import time
from typing import Dict, Any, Optional

from dotenv import load_dotenv
import requests

load_dotenv()

# LLM API configuration (OpenAI-compatible)
# 严格按照你提供的 gptsapi 官方 /v1/responses 示例：
#   base_url 默认 https://api.gptsapi.net
#   实际请求地址 base_url + /v1/responses
OPENAI_CONFIG = {
    "api_key": os.getenv("OPENAI_API_KEY", ""),
    "model_name": os.getenv("OPENAI_MODEL_NAME", "gpt-5.4"),
    "base_url": os.getenv("OPENAI_BASE_URL", "https://api.gptsapi.net"),
    "max_tokens": int(os.getenv("OPENAI_MAX_TOKENS", "3000")),
    "max_retries": 3,
    "retry_delay": 2,
}


def call_openai_api(prompt: str, system_prompt: Optional[str] = None) -> str:
    """
    Call the LLM API via gptsapi /v1/responses, matching the official curl example:

    curl https://api.gptsapi.net/v1/responses \\
      -H "Content-Type: application/json" \\
      -H "Authorization: Bearer YOUR_API_KEY" \\
      -d '{ "model": "...", "input": [...], "max_tokens": ... }'
    """
    if not OPENAI_CONFIG["api_key"]:
        return (
            "Error: LLM API key not configured. Please set OPENAI_API_KEY in your .env file. "
            "You can copy the key from the GPTsAPI dashboard."
        )

    base_url = OPENAI_CONFIG["base_url"].rstrip("/")
    url = f"{base_url}/v1/responses"

    # 官方示例只有 user 角色；为简单起见，这里把 system_prompt 合并进文本里一起发送
    if system_prompt:
        text = f"{system_prompt}\n\nUser query:\n{prompt}"
    else:
        text = prompt

    payload: Dict[str, Any] = {
        "model": OPENAI_CONFIG["model_name"],
        "input": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": text,
                    }
                ],
            }
        ],
        "max_tokens": OPENAI_CONFIG["max_tokens"],
    }

    headers = {
        "Authorization": f"Bearer {OPENAI_CONFIG['api_key']}",
        "Content-Type": "application/json",
    }

    last_error: Optional[Exception] = None

    for attempt in range(OPENAI_CONFIG["max_retries"]):
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=60)

            if resp.status_code != 200:
                try:
                    err_json = resp.json()
                    err_msg = err_json.get("error", {}).get("message") or str(err_json)
                except Exception:
                    err_msg = resp.text

                print(
                    f"⚠️ LLM API HTTP error (attempt {attempt+1}/{OPENAI_CONFIG['max_retries']}): "
                    f"status={resp.status_code}, msg={err_msg}"
                )
                last_error = RuntimeError(f"HTTP {resp.status_code}: {err_msg}")
            else:
                data = resp.json()

                # 解析 /v1/responses 结构：output 是一个列表，里面有 content/text
                text_chunks: list[str] = []
                output = data.get("output") if isinstance(data, dict) else None

                if isinstance(output, list) and output:
                    first = output[0] or {}
                    contents = first.get("content") or []
                    for c in contents:
                        if isinstance(c, dict):
                            t = c.get("text")
                            if t:
                                text_chunks.append(t)

                if text_chunks:
                    print(
                        f"✅ Successfully used LLM model via gptsapi /v1/responses: {OPENAI_CONFIG['model_name']}"
                    )
                    return "\n\n".join(text_chunks)

                return (
                    "Error: Unable to parse LLM response content. "
                    f"Raw response JSON: {data}"
                )

        except Exception as e:
            last_error = e
            err_type = type(e).__name__
            err_msg = str(e)
            print(
                f"⚠️ LLM API exception (attempt {attempt+1}/{OPENAI_CONFIG['max_retries']}): "
                f"{err_type}: {err_msg}"
            )

            if attempt < OPENAI_CONFIG["max_retries"] - 1:
                time.sleep(OPENAI_CONFIG["retry_delay"] * (2 ** attempt))

    if last_error is not None:
        return (
            f"Error: All LLM API attempts failed ({OPENAI_CONFIG['max_retries']} attempts). "
            f"Last error: {type(last_error).__name__}: {last_error}"
        )
    return "Error: All LLM API attempts failed for unknown reasons."

def analyze_downstream_results(
    analysis_type: str,
    analysis_data: Dict[str, Any],
    slice_id: Optional[str] = None,
    cluster_result_id: Optional[str] = None,
    additional_context: Optional[str] = None
) -> str:
    """Analyze downstream analysis results using an LLM (OpenAI-compatible)."""

    # Build context based on analysis type
    context_parts = []
    
    if slice_id:
        context_parts.append(f"Slice ID: {slice_id}")
    if cluster_result_id:
        context_parts.append(f"Cluster Result ID: {cluster_result_id}")
    
    context_parts.append(f"\nAnalysis Type: {analysis_type}")
    
    # Format analysis data based on type
    if analysis_type == "svg":
        # SVG (Spatially Variable Genes) Analysis
        context_parts.append("\n=== SVG Analysis Results ===")
        if isinstance(analysis_data, list):
            # If analysis_data is a list (enrichment results array)
            if len(analysis_data) > 0:
                context_parts.append(f"\nEnrichment Results ({len(analysis_data)} total):")
                # Show top 10 results
                for i, result in enumerate(analysis_data[:10], 1):
                    term = result.get("Term", "N/A")
                    category = result.get("Category", "N/A")
                    adj_pval = result.get("Adjusted P-value", "N/A")
                    overlap = result.get("Overlap", "N/A")
                    context_parts.append(
                        f"  {i}. {term} ({category}): "
                        f"Adjusted P-value: {adj_pval}, Overlap: {overlap}"
                    )
        elif isinstance(analysis_data, dict):
            # If analysis_data is a dict (cluster -> enrichment_data mapping)
            for cluster, enrichment_data in analysis_data.items():
                if isinstance(enrichment_data, list) and len(enrichment_data) > 0:
                    context_parts.append(f"\nCluster {cluster} Enrichment Results:")
                    # Show top 10 results
                    for i, result in enumerate(enrichment_data[:10], 1):
                        term = result.get("Term", "N/A")
                        category = result.get("Category", "N/A")
                        adj_pval = result.get("Adjusted P-value", "N/A")
                        overlap = result.get("Overlap", "N/A")
                        context_parts.append(
                            f"  {i}. {term} ({category}): "
                            f"Adjusted P-value: {adj_pval}, Overlap: {overlap}"
                        )
    
    elif analysis_type == "spatial":
        # Spatial Communication Analysis
        context_parts.append("\n=== Spatial Communication Analysis Results ===")
        if isinstance(analysis_data, dict):
            # Extract cluster information
            cluster_names = analysis_data.get("cluster_names", [])
            if cluster_names:
                context_parts.append(f"\nIdentified Clusters: {', '.join(map(str, cluster_names))} (Total: {len(cluster_names)} clusters)")
            
            # Extract top ligand-receptor interactions (most important data)
            top_interactions = analysis_data.get("top_interactions", [])
            if top_interactions and isinstance(top_interactions, list):
                context_parts.append(f"\nTop Ligand-Receptor Interactions (showing top {min(15, len(top_interactions))}):")
                for i, interaction in enumerate(top_interactions[:15], 1):
                    if isinstance(interaction, dict):
                        source = interaction.get("source_cluster", "N/A")
                        target = interaction.get("target_cluster", "N/A")
                        ligand = interaction.get("ligand", "N/A")
                        receptor = interaction.get("receptor", "N/A")
                        score = interaction.get("interaction_score", 0)
                        ligand_exp = interaction.get("ligand_exp", 0)
                        receptor_exp = interaction.get("receptor_exp", 0)
                        context_parts.append(
                            f"  {i}. Cluster {source} → Cluster {target}: "
                            f"{ligand} (exp: {ligand_exp:.4f}) → {receptor} (exp: {receptor_exp:.4f}), "
                            f"Interaction Score: {score:.6f}"
                        )
            
            # Extract cluster pair scores (aggregated communication strength)
            cluster_pair_scores = analysis_data.get("cluster_pair_scores", [])
            if cluster_pair_scores and isinstance(cluster_pair_scores, list):
                # Sort by interaction_score descending
                sorted_pairs = sorted(
                    cluster_pair_scores,
                    key=lambda x: x.get("interaction_score", 0),
                    reverse=True
                )
                context_parts.append(f"\nTop Cluster-Cluster Communication Pairs (by total interaction strength):")
                for i, pair in enumerate(sorted_pairs[:10], 1):
                    if isinstance(pair, dict):
                        source = pair.get("source_cluster", "N/A")
                        target = pair.get("target_cluster", "N/A")
                        score = pair.get("interaction_score", 0)
                        context_parts.append(
                            f"  {i}. Cluster {source} → Cluster {target}: "
                            f"Total Interaction Strength = {score:.6f}"
                        )
            
            # Extract strength matrix summary
            strength_matrix = analysis_data.get("strength_matrix", [])
            if strength_matrix and isinstance(strength_matrix, list):
                # Calculate statistics
                all_values = []
                for row in strength_matrix:
                    if isinstance(row, list):
                        all_values.extend([v for v in row if isinstance(v, (int, float))])
                if all_values:
                    max_strength = max(all_values)
                    min_strength = min([v for v in all_values if v > 0]) if any(v > 0 for v in all_values) else 0
                    avg_strength = sum(all_values) / len(all_values) if all_values else 0
                    context_parts.append(
                        f"\nCommunication Strength Matrix Statistics: "
                        f"Max = {max_strength:.4f}, Min (non-zero) = {min_strength:.4f}, "
                        f"Average = {avg_strength:.4f}"
                    )
            
            # Extract number matrix (spatial adjacency)
            number_matrix = analysis_data.get("number_matrix", [])
            if number_matrix and isinstance(number_matrix, list):
                # Count non-zero interactions
                total_adjacencies = 0
                for row in number_matrix:
                    if isinstance(row, list):
                        total_adjacencies += sum([1 for v in row if isinstance(v, (int, float)) and v > 0])
                context_parts.append(
                    f"\nSpatial Adjacency: {total_adjacencies} cluster-cluster spatial neighbor pairs identified"
                )
            
            # Summary of key findings
            if top_interactions:
                # Count unique ligands and receptors
                unique_ligands = set()
                unique_receptors = set()
                for interaction in top_interactions[:15]:
                    if isinstance(interaction, dict):
                        if "ligand" in interaction:
                            unique_ligands.add(interaction["ligand"])
                        if "receptor" in interaction:
                            unique_receptors.add(interaction["receptor"])
                context_parts.append(
                    f"\nKey Signaling Molecules: "
                    f"{len(unique_ligands)} unique ligands and {len(unique_receptors)} unique receptors "
                    f"involved in top interactions"
                )
    
    elif analysis_type == "deg":
        # DEG (Differentially Expressed Genes) Analysis
        context_parts.append("\n=== DEG Analysis Results ===")
        context_parts.append("This analysis includes both Heatmap and Dotplot views with complete gene expression data.")
        if isinstance(analysis_data, dict):
            # Heatmap-style data (cluster-gene-expression endpoint)
            if "expression" in analysis_data:
                expr_data = analysis_data["expression"]
                if isinstance(expr_data, dict):
                    genes = expr_data.get("genes", [])
                    clusters = expr_data.get("clusters", [])
                    context_parts.append(
                        f"\nHeatmap View - {len(set(clusters))} clusters, {len(genes)} marker genes total."
                    )
            # Dotplot data (cluster_gene_dotplot endpoint)
            if "dot" in analysis_data:
                dot_data = analysis_data["dot"]
                context_parts.append("\nDotplot View - Gene expression patterns (per cluster) available.")
                if isinstance(dot_data, dict) and "data" in dot_data:
                    genes = dot_data.get("genes", [])
                    clusters = dot_data.get("clusters", [])
                    rows = dot_data.get("data", [])
                    context_parts.append(
                        f"Dotplot summary: {len(genes)} genes, {len(clusters)} clusters, {len(rows)} gene-cluster data points."
                    )
                    # For each cluster, show top 3 genes by average expression with percentages
                    cluster_to_rows = {}
                    for row in rows:
                        if not isinstance(row, dict):
                            continue
                        cluster = row.get("cluster")
                        gene = row.get("gene")
                        if cluster is None or gene is None:
                            continue
                        cluster_to_rows.setdefault(cluster, []).append(row)

                    # Sort clusters for stable output
                    for cluster in sorted(cluster_to_rows.keys(), key=lambda x: str(x)):
                        cluster_rows = cluster_to_rows[cluster]
                        # Sort by avg_expr descending
                        cluster_rows_sorted = sorted(
                            cluster_rows,
                            key=lambda r: r.get("avg_expr", 0.0),
                            reverse=True,
                        )
                        top_rows = cluster_rows_sorted[:3]
                        if not top_rows:
                            continue
                        gene_summaries = []
                        for r in top_rows:
                            gene_name = r.get("gene", "NA")
                            avg_expr = r.get("avg_expr", 0.0)
                            pct_expr = r.get("pct_expr", 0.0)
                            try:
                                gene_summaries.append(
                                    f"{gene_name} (avg_expr={avg_expr:.3f}, pct_expr={pct_expr:.2%})"
                                )
                            except Exception:
                                gene_summaries.append(
                                    f"{gene_name} (avg_expr={avg_expr}, pct_expr={pct_expr})"
                                )
                        context_parts.append(
                            f"Cluster {cluster}: top DEG markers by average expression: "
                            + "; ".join(gene_summaries)
                        )
    
    elif analysis_type == "deconvolution":
        # Deconvolution Analysis
        context_parts.append("\n=== Deconvolution Analysis Results ===")
        if isinstance(analysis_data, dict):
            if "cell_types" in analysis_data:
                cell_types = analysis_data["cell_types"]
                if isinstance(cell_types, list):
                    context_parts.append(f"Number of cell types identified: {len(cell_types)}")
                    for ct in cell_types[:10]:  # Show first 10
                        if isinstance(ct, dict):
                            name = ct.get("name", "N/A")
                            context_parts.append(f"  - {name}")
            if "hierarchy" in analysis_data:
                context_parts.append("Cell type hierarchy available")
    
    # elif analysis_type == "neighborhood":
    #     # Neighborhood Analysis
    #     context_parts.append("\n=== Neighborhood Analysis Results ===")
    #     if isinstance(analysis_data, dict):
    #         context_parts.append("Attention flow neighborhood analysis data")
    
    if additional_context:
        context_parts.append(f"\nAdditional Context:\n{additional_context}")
    
    context_text = "\n".join(context_parts)
    
    # Build prompt
    prompt = f"""
You are analyzing spatial omics data. Please provide a CONCISE, data-driven analysis based on the SPECIFIC results provided below. 

CRITICAL: Your response MUST be under 2000 tokens. Be brief and focused. Prioritize the most important findings.

{context_text}

Please analyze the above data and provide a SHORT response covering:

1. **Summary** (2-3 sentences): Highlight the SPECIFIC findings (mention actual cluster numbers, gene names, pathways, or interaction scores where relevant)

2. **Key Insights** (TOP 3-5 findings only, use bullet points):
   - Which specific cluster pairs show the strongest communication?
   - Which ligand-receptor pairs are most active?
   - What are the most enriched pathways or biological processes?
   - What are the most differentially expressed genes?
   - Reference specific values, scores, or statistics when available

3. **Biological Interpretation** (1-2 paragraphs maximum):
   - What biological processes or cell types might be represented by the identified clusters?
   - What signaling pathways are active based on the ligand-receptor interactions?
   - What does the spatial organization suggest about tissue function?

4. **Notable Patterns** (2-3 patterns only, use bullet points):
   - Are there clusters that are particularly active in communication?
   - Are there specific signaling pathways that dominate?
   - Are there spatial relationships that stand out?

5. **Potential Implications** (1 paragraph maximum):
   - Understanding the tissue's biological function
   - Disease mechanisms (if applicable)
   - Therapeutic targets (if relevant)

6. **Recommendations** (2-3 specific next steps, use bullet points):
   - Which cluster pairs or interactions should be investigated further?
   - Which pathways or genes warrant experimental validation?
   - What additional analyses would be most informative?

CRITICAL REQUIREMENTS:
- Base your analysis on the ACTUAL DATA provided above. Avoid generic statements.
- Keep your response UNDER 2000 tokens. Be concise - use bullet points where possible.
- If you need to cut content, prioritize Summary, Key Insights, and Recommendations.
- Do NOT exceed the token limit. Stop writing if you're approaching the limit.
"""
    
    # Call OpenAI API
    interpretation = call_openai_api(prompt)
    return interpretation

