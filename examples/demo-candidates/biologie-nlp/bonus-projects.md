# Banque de projets bonus - Léa Dubois

Ces projets sont synthétiques et destinés au test de JobAuto Studio. Ils ne sont
pas visibles par défaut dans le CV source. Ajoute-les dans la banque de projets
pour vérifier que JobAuto sélectionne les trois projets les plus pertinents pour
chaque offre.

## 1. Matching de patients et essais cliniques par NLP

- **Stack** : Python, PyTorch, Transformers, Sentence Transformers, Elasticsearch, FastAPI
- **Description 1** : Conception d'un pipeline extrayant critères d'éligibilité, pathologies, traitements et données démographiques depuis des protocoles d'essais cliniques.
- **Description 2** : Classement sémantique de profils patients dé-identifiés avec règles d'exclusion explicites, évaluation Recall@K et interface de validation humaine.
- **Mode conseillé** : `reframe`
- **Visible par défaut** : non
- **Rôles visés** : NLP Engineer, Applied Scientist, Data Scientist santé
- **Angles possibles** : NLP clinique, recherche sémantique, produit IA contrôlable, évaluation et human-in-the-loop

## 2. Prédiction de fonction protéique par embeddings

- **Stack** : Python, PyTorch, ESM-2, scikit-learn, Biopython, MLflow
- **Description 1** : Extraction d'embeddings de séquences protéiques avec ESM-2 puis entraînement de modèles multi-label pour prédire des familles fonctionnelles.
- **Description 2** : Comparaison avec des baselines fondées sur BLAST, calibration des probabilités et analyse des erreurs selon longueur et similarité des séquences.
- **Mode conseillé** : `reframe`
- **Visible par défaut** : non
- **Rôles visés** : ML Engineer Life Sciences, Computational Biologist, Research Engineer
- **Angles possibles** : deep learning scientifique, représentation de séquences, expérimentation reproductible, bioinformatique

## 3. Détection de signaux de pharmacovigilance

- **Stack** : Python, spaCy, Transformers, PostgreSQL, Airflow, Tableau
- **Description 1** : Extraction d'effets indésirables, médicaments et temporalités dans des comptes rendus dé-identifiés avec normalisation terminologique.
- **Description 2** : Agrégation des signaux, contrôles qualité et tableau de bord permettant aux analystes de prioriser les associations à examiner.
- **Mode conseillé** : `derive`
- **Visible par défaut** : non
- **Rôles visés** : Data Scientist pharma, NLP Engineer, Data \& AI Consultant santé
- **Angles possibles** : NLP appliqué, pipeline data, surveillance du risque, dashboarding et collaboration métier

## 4. Graphe de connaissances gènes-maladies-médicaments

- **Stack** : Python, Neo4j, Cypher, BioBERT, PubMed, Docker
- **Description 1** : Construction d'un graphe reliant entités biomédicales extraites de publications et bases publiques, avec provenance de chaque relation.
- **Description 2** : Développement de requêtes exploratoires et d'un moteur de recherche pour identifier mécanismes, traitements et publications associés à une pathologie.
- **Mode conseillé** : `reframe`
- **Visible par défaut** : non
- **Rôles visés** : Knowledge Engineer, NLP Engineer, Bioinformatics Data Scientist
- **Angles possibles** : knowledge graph, intégration de données, NLP biomédical, explicabilité et traçabilité

## 5. Agent de veille scientifique multi-sources

- **Stack** : Python, LangGraph, APIs LLM, PubMed, bioRxiv, Crossref, Qdrant
- **Description 1** : Orchestration d'agents spécialisés pour rechercher, dédupliquer, qualifier et synthétiser des publications récentes selon un protocole scientifique configurable.
- **Description 2** : Ajout de citations vérifiables, contrôle de couverture, journal des décisions et validation humaine avant diffusion de la synthèse.
- **Mode conseillé** : `derive`
- **Visible par défaut** : non
- **Rôles visés** : GenAI Engineer Life Sciences, AI Research Engineer, Innovation Consultant
- **Angles possibles** : agents IA, RAG agentique, veille scientifique, évaluation LLM et workflow contrôlé

## Réglages communs conseillés

- **Title freedom** : adaptable
- **Stack freedom** : adaptable
- **Description freedom** : highly adaptable
- **Allow new project** : oui
- **Allow GitHub or web inspiration** : oui
- **Minimum visible projects** : 3
- **Maximum visible projects** : 3

Pour un test réaliste, n'ajoute pas systématiquement les cinq projets au CV.
Ils constituent une banque dans laquelle le moteur doit sélectionner selon
l'offre.
