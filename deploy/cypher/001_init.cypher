// 职引者 ZHIYIN · Neo4j 约束与索引（幂等）
// 与 stores/schema.py::NEO4J_CYPHER 保持一致。
// 图模型：(:Role)-[:REQUIRES {level, hits}]->(:Skill)，只写脱敏后的结构化技能字段。

CREATE CONSTRAINT skill_name IF NOT EXISTS FOR (s:Skill) REQUIRE s.name IS UNIQUE;
CREATE CONSTRAINT role_name  IF NOT EXISTS FOR (r:Role)  REQUIRE r.name IS UNIQUE;
CREATE INDEX skill_level IF NOT EXISTS FOR (s:Skill) ON (s.level);
