-- ==============================================================================
-- OmniSite PostGIS 16대 물리 데이터베이스 마스터 스키마 DDL (schema.sql)
-- ==============================================================================
-- 로컬/상용 DB 기동 시 본 스크립트를 주입하여 전체 데이터 관계 및 인덱스를 구축합니다.

-- 0. 핵심 지리 공간 및 벡터 유사도 검색 확장팩 활성화
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS vector;

-- 1. 구정 실무자 계정 마스터 테이블
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    email VARCHAR(150) UNIQUE NOT NULL,      -- 로그인용 이메일 ID
    hashed_password VARCHAR(255) NOT NULL,    -- bcrypt 단방향 해싱 암호문
    username VARCHAR(100) NOT NULL,          -- 실무자 실명
    is_active BOOLEAN DEFAULT TRUE,          -- 계정 활성화 여부
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 2. 자치구역 마스터 테이블
CREATE TABLE IF NOT EXISTS districts (
    id SERIAL PRIMARY KEY,
    district_name VARCHAR(100) NOT NULL, -- 예: "서울특별시 용산구"
    sig_cd VARCHAR(5) UNIQUE NOT NULL,    -- 법정 시군구 코드 (예: "11170")
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 3. 서울시 행정구역 (동별) 공간정보 테이블
CREATE TABLE IF NOT EXISTS dong_boundaries (
    id SERIAL PRIMARY KEY,
    district_id INT REFERENCES districts(id) ON DELETE CASCADE,
    dong_code VARCHAR(10) UNIQUE NOT NULL, -- 외래키 참조를 위한 UNIQUE 제약 추가
    dong_name VARCHAR(100) NOT NULL,
    geom GEOMETRY(MultiPolygon, 4326) NOT NULL -- 행정동 경계 면 공간 객체
);
CREATE INDEX IF NOT EXISTS idx_dong_geom ON dong_boundaries USING GIST(geom);

-- 4. 서울 금연구역 정보 테이블
CREATE TABLE IF NOT EXISTS nosmoking_zones (
    id SERIAL PRIMARY KEY,
    district_id INT REFERENCES districts(id) ON DELETE CASCADE,
    dong_id INT REFERENCES dong_boundaries(id) ON DELETE SET NULL, -- 행정동 ID 관계 추가
    zone_name VARCHAR(150),
    address VARCHAR(250),
    geom GEOMETRY(Point, 4326) NOT NULL, -- Point 좌표 객체
    area NUMERIC,
    registered_at DATE
);
CREATE INDEX IF NOT EXISTS idx_nosmoking_geom ON nosmoking_zones USING GIST(geom);

-- 5. 서울 어린이집/학교 정보 테이블
CREATE TABLE IF NOT EXISTS childcare_centers (
    id SERIAL PRIMARY KEY,
    district_id INT REFERENCES districts(id) ON DELETE CASCADE,
    dong_id INT REFERENCES dong_boundaries(id) ON DELETE SET NULL, -- 행정동 ID 관계 추가
    center_name VARCHAR(150) NOT NULL,
    center_type VARCHAR(50), -- "어린이집", "초등학교", "유치원" 등
    address VARCHAR(250),
    geom GEOMETRY(Point, 4326) NOT NULL,
    student_count INT
);
CREATE INDEX IF NOT EXISTS idx_childcare_geom ON childcare_centers USING GIST(geom);

-- 6. 버스/지하철 역사 마스터 위치 테이블
CREATE TABLE IF NOT EXISTS transit_stations (
    id SERIAL PRIMARY KEY,
    district_id INT REFERENCES districts(id) ON DELETE CASCADE,
    dong_id INT REFERENCES dong_boundaries(id) ON DELETE SET NULL, -- 행정동 ID 관계 추가
    station_no VARCHAR(50) UNIQUE NOT NULL, -- 이용객 조인을 위한 UNIQUE 제약 추가
    station_name VARCHAR(150) NOT NULL,
    transit_type VARCHAR(10) NOT NULL, -- "BUS" or "SUBWAY"
    geom GEOMETRY(Point, 4326) NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_transit_geom ON transit_stations USING GIST(geom);

-- 7. 대중교통 이용객 통계 정보 테이블
CREATE TABLE IF NOT EXISTS transit_passengers (
    id SERIAL PRIMARY KEY,
    station_id INT REFERENCES transit_stations(id) ON DELETE CASCADE, -- station_no 직접 참조 대신 ID 외래키 조인으로 개선
    analysis_ym VARCHAR(6) NOT NULL, -- YYYYMM
    boarding_count INT DEFAULT 0,
    alighting_count INT DEFAULT 0,
    total_volume INT DEFAULT 0
);

-- 8. 행정동단위 서울 생활인구 통계 테이블 (Aggregation 요약본)
CREATE TABLE IF NOT EXISTS population_stats (
    id SERIAL PRIMARY KEY,
    dong_id INT REFERENCES dong_boundaries(id) ON DELETE CASCADE, -- dong_code 직접 참조 대신 ID 외래키 조인으로 개선
    day_type VARCHAR(10) NOT NULL,  -- "WEEKDAY" or "WEEKEND"
    time_type VARCHAR(10) NOT NULL, -- "RUSH_HOUR", "DAYTIME", "NIGHT"
    avg_population NUMERIC NOT NULL
);

-- 9. 소상공인 상가상권 정보 테이블
CREATE TABLE IF NOT EXISTS commercial_shops (
    id SERIAL PRIMARY KEY,
    district_id INT REFERENCES districts(id) ON DELETE CASCADE,
    dong_id INT REFERENCES dong_boundaries(id) ON DELETE SET NULL, -- 행정동 ID 관계 추가
    shop_name VARCHAR(150) NOT NULL,
    category_code VARCHAR(10), -- 업종 코드
    category_name VARCHAR(50),
    geom GEOMETRY(Point, 4326) NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_shop_geom ON commercial_shops USING GIST(geom);

-- 10. 자치구 불법흡연 민원 통계 테이블
CREATE TABLE IF NOT EXISTS civil_complaints (
    id SERIAL PRIMARY KEY,
    dong_id INT REFERENCES dong_boundaries(id) ON DELETE CASCADE, -- dong_code 직접 참조 대신 ID 외래키 조인으로 개선
    complaint_count INT NOT NULL,
    analysis_year VARCHAR(4) NOT NULL -- YYYY
);

-- 11. 국토교통부 연속지적도 테이블 (사전 슬라이싱 완료본)
CREATE TABLE IF NOT EXISTS cadastral_lands (
    id SERIAL PRIMARY KEY,
    district_id INT REFERENCES districts(id) ON DELETE CASCADE,
    dong_id INT REFERENCES dong_boundaries(id) ON DELETE SET NULL, -- 행정동 ID 관계 추가
    pnu VARCHAR(19) NOT NULL,          -- 필지 고유 번호
    jibun VARCHAR(100),
    land_use_code VARCHAR(5),          -- 지목 (도, 공, 체 등)
    ownership_type VARCHAR(10),        -- 소유 구분 (국유지, 시유지 등)
    geom GEOMETRY(MultiPolygon, 4326) NOT NULL -- 다중 폴리곤 통합 처리용 MultiPolygon 변경
);
CREATE INDEX IF NOT EXISTS idx_cadastral_geom ON cadastral_lands USING GIST(geom);

-- 12. 전국휴지통데이터 테이블 (가점 요인)
CREATE TABLE IF NOT EXISTS trash_bins (
    id SERIAL PRIMARY KEY,
    district_id INT REFERENCES districts(id) ON DELETE CASCADE,
    dong_id INT REFERENCES dong_boundaries(id) ON DELETE SET NULL, -- 행정동 ID 관계 추가
    bin_name VARCHAR(150),
    geom GEOMETRY(Point, 4326) NOT NULL,
    bin_type VARCHAR(50) -- "가로쓰레기통", "담배꽁초수거함" 등
);
CREATE INDEX IF NOT EXISTS idx_trash_geom ON trash_bins USING GIST(geom);

-- 13. 주민등록인구 연령별 동별 통계 테이블 (감점 요인)
CREATE TABLE IF NOT EXISTS age_demographics (
    id SERIAL PRIMARY KEY,
    dong_id INT REFERENCES dong_boundaries(id) ON DELETE CASCADE, -- dong_code 직접 참조 대신 ID 외래키 조인으로 개선
    youth_population INT NOT NULL,     -- 만 19세 미만 인구
    total_population INT NOT NULL,     -- 행정동 총인구
    youth_ratio NUMERIC NOT NULL       -- youth_population / total_population
);

-- 14. 담배꽁초상습무단투기지역현황 테이블 (최상위 가중치)
CREATE TABLE IF NOT EXISTS cigarette_dumping_zones (
    id SERIAL PRIMARY KEY,
    district_id INT REFERENCES districts(id) ON DELETE CASCADE,
    dong_id INT REFERENCES dong_boundaries(id) ON DELETE SET NULL, -- 행정동 ID 관계 추가
    address VARCHAR(250),
    detail_location TEXT,
    geom GEOMETRY(Point, 4326) NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_dumping_geom ON cigarette_dumping_zones USING GIST(geom);

-- 15. AHP 가중치 프로파일 마스터 테이블
CREATE TABLE IF NOT EXISTS ahp_models (
    id SERIAL PRIMARY KEY,
    district_id INT REFERENCES districts(id) ON DELETE CASCADE,
    criteria_weights JSONB NOT NULL, -- 인자별 설정 가중치 매트릭스 백업
    consistency_ratio NUMERIC NOT NULL, -- 일관성 비율 (C.R. < 0.1 검증값)
    is_locked BOOLEAN DEFAULT FALSE, -- 의사결정 시 조작 방지 락 상태
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 16. 시뮬레이션 결과 리포트 캐시 테이블
CREATE TABLE IF NOT EXISTS conflict_simulations (
    id SERIAL PRIMARY KEY,
    cadastral_land_id INT REFERENCES cadastral_lands(id) ON DELETE CASCADE, -- 지적 필지 FK
    css_score NUMERIC NOT NULL, -- 종합 갈등 민감도 점수 (CSS)
    css_vector JSONB NOT NULL, -- 3대 민감도 인자 벡터 백업
    normal_scenario TEXT, -- 일반 시나리오 토론 로그
    optimal_scenario TEXT, -- 우호적 타결 시나리오 토론 로그
    worst_scenario TEXT, -- 극단적 불합의 시나리오 토론 로그
    confidence_score NUMERIC, -- 시뮬레이션 통계적 신뢰도 점수
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 17. Audit AI 공문서 검증을 필터링 통과한 실제 이행 사례 기록 테이블
CREATE TABLE IF NOT EXISTS verified_precedents (
    id SERIAL PRIMARY KEY,
    conflict_simulation_id INT REFERENCES conflict_simulations(id) ON DELETE SET NULL, -- 이전 가상 시뮬레이션 매핑 FK
    document_title VARCHAR(250), -- 행정 공문서 타이틀
    document_ocr_text TEXT, -- OCR로 정제 추출된 실제 결과 텍스트
    actual_scenario VARCHAR(50) NOT NULL, -- 실제 매핑된 시나리오 유형 ('NORMAL', 'OPTIMAL', 'WORST')
    verified_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
