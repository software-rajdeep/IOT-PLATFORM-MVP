-- =============================================================================
-- DEMO CUSTOMER DATABASE (ucap_customer_corp)
-- Simulates "the customer's own server + database" that they will NOT give us
-- direct access to. It is a busy production database with many tables and lots
-- of rows. The platform only connects to it through the customer's API
-- (api key + secret) and maps a response field to measurement_raw.
--
-- The business-owned table we map from is  shopfloor.measurement_readings.
--
-- Idempotent: drops + rebuilds so re-running refreshes the random data.
-- =============================================================================

CREATE SCHEMA IF NOT EXISTS products;
CREATE SCHEMA IF NOT EXISTS suppliers;
CREATE SCHEMA IF NOT EXISTS production;
CREATE SCHEMA IF NOT EXISTS shopfloor;
CREATE SCHEMA IF NOT EXISTS quality;
CREATE SCHEMA IF NOT EXISTS maintenance;
CREATE SCHEMA IF NOT EXISTS inventory;
CREATE SCHEMA IF NOT EXISTS energy;
CREATE SCHEMA IF NOT EXISTS sales;
CREATE SCHEMA IF NOT EXISTS hr;
CREATE SCHEMA IF NOT EXISTS logistics;

-- ---------------------------------------------------------------------------
-- PRODUCTS (catalogue masters)
-- ---------------------------------------------------------------------------
DROP TABLE IF EXISTS products.products CASCADE;
CREATE TABLE products.products (
    product_code     VARCHAR(32) PRIMARY KEY,
    product_name     VARCHAR(255) NOT NULL,
    category         VARCHAR(64),
    target_thickness_mm NUMERIC(16,6),
    lower_tolerance_mm NUMERIC(16,6),
    upper_tolerance_mm NUMERIC(16,6),
    unit             VARCHAR(16) NOT NULL DEFAULT 'mm',
    active           BOOLEAN NOT NULL DEFAULT true
);

INSERT INTO products.products (product_code, product_name, category, target_thickness_mm, lower_tolerance_mm, upper_tolerance_mm, unit)
SELECT 'RUB-EXTR-' || lp, 'Rubber Extrusion ' || lp, 'rubber_extrusion',
       8.5 + (random() * 4.0 - 2.0),
       8.5 + (random() * 4.0 - 2.0) - 0.20,
       8.5 + (random() * 4.0 - 2.0) + 0.20,
       'mm'
FROM generate_series(1, 12) AS lp;

-- ---------------------------------------------------------------------------
-- SUPPLIERS + PURCHASE MATERIALS
-- ---------------------------------------------------------------------------
DROP TABLE IF EXISTS suppliers.suppliers CASCADE;
CREATE TABLE suppliers.suppliers (
    supplier_code VARCHAR(32) PRIMARY KEY,
    supplier_name VARCHAR(255) NOT NULL,
    city          VARCHAR(128),
    country       VARCHAR(64),
    contact_email VARCHAR(255),
    active        BOOLEAN NOT NULL DEFAULT true
);
INSERT INTO suppliers.suppliers (supplier_code, supplier_name, city, country, contact_email)
SELECT 'SUP-' || generate_series, 'Vendor ' || generate_series, 'City ' || (generate_series % 6), 'IN', 'vendor' || generate_series || '@example.com'
FROM generate_series(1, 20);

-- ---------------------------------------------------------------------------
-- PRODUCTION (master data + orders)
-- ---------------------------------------------------------------------------
DROP TABLE IF EXISTS production.machines CASCADE;
CREATE TABLE production.machines (
    machine_code VARCHAR(32) PRIMARY KEY,
    machine_name VARCHAR(255) NOT NULL,
    line         VARCHAR(64),
    location     VARCHAR(128),
    installed_on DATE
);
INSERT INTO production.machines (machine_code, machine_name, line, location, installed_on)
VALUES
  ('EXTRUSION_LINE_2', 'Extrusion Line 2', 'EXTRUSION', 'Building A', '2021-03-15'),
  ('EXTRUSION_LINE_1', 'Extrusion Line 1', 'EXTRUSION', 'Building A', '2020-11-02'),
  ('CALENDAR_1',      'Calender 1',       'CALENDER', 'Building B', '2022-01-20'),
  ('LASER_CD22_01',   'CD22 Thickness Sensor 01', 'QUALITY', 'Extrusion Line 2', '2023-06-01');

DROP TABLE IF EXISTS production.shifts CASCADE;
CREATE TABLE production.shifts (
    shift_code  VARCHAR(16) PRIMARY KEY,
    shift_name  VARCHAR(64),
    start_time  TIME,
    end_time    TIME
);
INSERT INTO production.shifts (shift_code, shift_name, start_time, end_time) VALUES
  ('A', 'Morning',  '06:00', '14:00'),
  ('B', 'Evening',  '14:00', '22:00'),
  ('C', 'Night',    '22:00', '06:00');

DROP TABLE IF EXISTS production.operators CASCADE;
CREATE TABLE production.operators (
    operator_code VARCHAR(32) PRIMARY KEY,
    full_name     VARCHAR(255),
    role          VARCHAR(64),
    machine_code  VARCHAR(32) REFERENCES production.machines(machine_code)
);
INSERT INTO production.operators (operator_code, full_name, role, machine_code)
SELECT 'OP-' || lp, 'Operator ' || lp, (ARRAY['line_lead','technician','supervisor'])[1 + lp % 3], 'EXTRUSION_LINE_2'
FROM generate_series(1, 15) AS lp;

DROP TABLE IF EXISTS production.production_orders CASCADE;
CREATE TABLE production.production_orders (
    order_no      VARCHAR(32) PRIMARY KEY,
    product_code  VARCHAR(32) REFERENCES products.products(product_code),
    machine_code  VARCHAR(32) REFERENCES production.machines(machine_code),
    planned_qty   INTEGER,
    produced_qty  INTEGER,
    target_thickness_mm NUMERIC(16,6),
    status        VARCHAR(32),
    planned_start TIMESTAMPTZ,
    planned_end   TIMESTAMPTZ
);
INSERT INTO production.production_orders
    (order_no, product_code, machine_code, planned_qty, produced_qty, target_thickness_mm, status, planned_start, planned_end)
SELECT
    'PO-' || lp,
    (SELECT product_code FROM products.products ORDER BY random() LIMIT 1),
    'EXTRUSION_LINE_2',
    500 + (random() * 1500)::int,
    400 + (random() * 1600)::int,
    8.5 + (random() * 4.0 - 2.0),
    (ARRAY['planned','running','completed','completed','closed'])[1 + lp % 5],
    now() - interval '1 day' * lp,
    now() + interval '1 day' * (5 - lp)
FROM generate_series(1, 40) AS lp;

DROP TABLE IF EXISTS production.shift_assignments CASCADE;
CREATE TABLE production.shift_assignments (
    assignment_id BIGSERIAL PRIMARY KEY,
    shift_code    VARCHAR(16) REFERENCES production.shifts(shift_code),
    operator_code VARCHAR(32) REFERENCES production.operators(operator_code),
    work_date     DATE
);
INSERT INTO production.shift_assignments (shift_code, operator_code, work_date)
SELECT (ARRAY['A','B','C'])[1 + lp % 3], 'OP-' || (1 + lp % 15), current_date - (lp % 7)
FROM generate_series(1, 200) AS lp;

-- ---------------------------------------------------------------------------
-- SHOPFLOOR — the business table we map from
-- Simulates CD22-style raw counts + decoded mm stored in the customer's DB.
-- ---------------------------------------------------------------------------
DROP TABLE IF EXISTS shopfloor.measurement_readings CASCADE;
CREATE TABLE shopfloor.measurement_readings (
    reading_id    BIGSERIAL PRIMARY KEY,
    device_code   VARCHAR(32) NOT NULL,
    order_no      VARCHAR(32),
    reading_ts    TIMESTAMPTZ NOT NULL DEFAULT now(),
    raw_count     INTEGER,                 -- CD22 raw count  (mapped -> measurement_raw)
    value_mm      NUMERIC(16,6),           -- decoded displacement value (mm)
    quality_flag  VARCHAR(16)
);

INSERT INTO shopfloor.measurement_readings (device_code, order_no, reading_ts, raw_count, value_mm, quality_flag)
SELECT
    'LASER_CD22_01',
    'PO-' || (1 + (lp / 100) % 40),
    now() - (interval '1 minute' * lp / 10),
    (140 + (random() * 120))::int,
    round((1.4 + random() * 1.2)::numeric, 6),
    (ARRAY['OK','OK','OK','WARN'])[1 + lp % 4]
FROM generate_series(1, 5000) AS lp;

-- ---------------------------------------------------------------------------
-- QUALITY lab results
-- ---------------------------------------------------------------------------
DROP TABLE IF EXISTS quality.quality_checks CASCADE;
CREATE TABLE quality.quality_checks (
    check_id    BIGSERIAL PRIMARY KEY,
    order_no    VARCHAR(32),
    sample_no   VARCHAR(32),
    hardness_shore NUMERIC(8,2),
    tensile_mpa NUMERIC(8,2),
    thickness_mm NUMERIC(8,3),
    checked_on  TIMESTAMPTZ DEFAULT now(),
    operator_code VARCHAR(32)
);
INSERT INTO quality.quality_checks (order_no, sample_no, hardness_shore, tensile_mpa, thickness_mm, checked_on, operator_code)
SELECT
    'PO-' || (1 + lp % 40),
    'SMP-' || lp,
    round((60 + random() * 25)::numeric, 2),
    round((8 + random() * 10)::numeric, 2),
    round((7.8 + random() * 1.4)::numeric, 3),
    now() - (interval '1 hour' * lp),
    'OP-' || (1 + lp % 15)
FROM generate_series(1, 800) AS lp;

-- ---------------------------------------------------------------------------
-- MAINTENANCE + ALARMS
-- ---------------------------------------------------------------------------
DROP TABLE IF EXISTS maintenance.maintenance_tasks CASCADE;
CREATE TABLE maintenance.maintenance_tasks (
    task_id      BIGSERIAL PRIMARY KEY,
    machine_code VARCHAR(32) REFERENCES production.machines(machine_code),
    task_type    VARCHAR(64),
    status       VARCHAR(32),
    planned_on   DATE,
    completed_on DATE,
    cost         NUMERIC(12,2)
);
INSERT INTO maintenance.maintenance_tasks (machine_code, task_type, status, planned_on, completed_on, cost)
SELECT
    (ARRAY['EXTRUSION_LINE_2','EXTRUSION_LINE_1','CALENDAR_1'])[1 + lp % 3],
    (ARRAY['preventive','corrective','lubrication','inspection'])[1 + lp % 4],
    (ARRAY['planned','in_progress','completed'])[1 + lp % 3],
    current_date - (lp % 30),
    current_date - (lp % 15),
    round((random() * 50000)::numeric, 2)
FROM generate_series(1, 120) AS lp;

DROP TABLE IF EXISTS maintenance.alarm_events CASCADE;
CREATE TABLE maintenance.alarm_events (
    alarm_id     BIGSERIAL PRIMARY KEY,
    device_code  VARCHAR(32),
    alarm_code   VARCHAR(32),
    severity     VARCHAR(16),
    message      TEXT,
    event_ts     TIMESTAMPTZ DEFAULT now()
);
INSERT INTO maintenance.alarm_events (device_code, alarm_code, severity, message, event_ts)
SELECT
    (ARRAY['LASER_CD22_01','EXTRUSION_LINE_2'])[1 + lp % 2],
    'ALM-' || (100 + lp % 20),
    (ARRAY['LOW','MEDIUM','HIGH'])[1 + lp % 3],
    'Simulated alarm event ' || lp,
    now() - (interval '1 day' * (lp % 30))
FROM generate_series(1, 300) AS lp;

-- ---------------------------------------------------------------------------
-- INVENTORY
-- ---------------------------------------------------------------------------
DROP TABLE IF EXISTS inventory.raw_materials CASCADE;
CREATE TABLE inventory.raw_materials (
    material_code VARCHAR(32) PRIMARY KEY,
    material_name VARCHAR(255),
    uom           VARCHAR(16) DEFAULT 'kg',
    min_stock     NUMERIC(12,2),
    current_stock NUMERIC(12,2)
);
INSERT INTO inventory.raw_materials (material_code, material_name, uom, min_stock, current_stock)
SELECT 'RM-' || lp, 'Raw Material ' || lp, 'kg', 100, 100 + random() * 2000
FROM generate_series(1, 25) AS lp;

DROP TABLE IF EXISTS inventory.material_batches CASCADE;
CREATE TABLE inventory.material_batches (
    batch_no      VARCHAR(32) PRIMARY KEY,
    material_code VARCHAR(32) REFERENCES inventory.raw_materials(material_code),
    quantity      NUMERIC(12,2),
    received_on   DATE,
    supplier_code VARCHAR(32) REFERENCES suppliers.suppliers(supplier_code),
    quality_status VARCHAR(16)
);
INSERT INTO inventory.material_batches (batch_no, material_code, quantity, received_on, supplier_code, quality_status)
SELECT
    'BAT-' || lp,
    'RM-' || (1 + lp % 25),
    round((random() * 900)::numeric, 2),
    current_date - (lp % 60),
    'SUP-' || (1 + lp % 20),
    (ARRAY['approved','quarantine','rejected'])[1 + lp % 3]
FROM generate_series(1, 400) AS lp;

DROP TABLE IF EXISTS inventory.stock_movements CASCADE;
CREATE TABLE inventory.stock_movements (
    movement_id  BIGSERIAL PRIMARY KEY,
    material_code VARCHAR(32),
    direction    VARCHAR(8),
    quantity     NUMERIC(12,2),
    moved_on     TIMESTAMPTZ DEFAULT now()
);
INSERT INTO inventory.stock_movements (material_code, direction, quantity, moved_on)
SELECT 'RM-' || (1 + lp % 25), (ARRAY['IN','OUT'])[1 + lp % 2], round((random() * 300)::numeric, 2), now() - (interval '1 hour' * lp)
FROM generate_series(1, 900) AS lp;

-- ---------------------------------------------------------------------------
-- ENERGY
-- ---------------------------------------------------------------------------
DROP TABLE IF EXISTS energy.power_meter_readings CASCADE;
CREATE TABLE energy.power_meter_readings (
    reading_id   BIGSERIAL PRIMARY KEY,
    meter_code   VARCHAR(32),
    reading_ts   TIMESTAMPTZ DEFAULT now(),
    kwh          NUMERIC(10,3),
    voltage_v    NUMERIC(8,1),
    current_a    NUMERIC(8,2)
);
INSERT INTO energy.power_meter_readings (meter_code, reading_ts, kwh, voltage_v, current_a)
SELECT
    'MTR-' || (1 + lp % 8),
    now() - (interval '1 minute' * lp / 5),
    round((random() * 500)::numeric, 3),
    round((220 + random() * 20)::numeric, 1),
    round((5 + random() * 40)::numeric, 2)
FROM generate_series(1, 3000) AS lp;

-- ---------------------------------------------------------------------------
-- SALES
-- ---------------------------------------------------------------------------
DROP TABLE IF EXISTS sales.customers CASCADE;
CREATE TABLE sales.customers (
    customer_code VARCHAR(32) PRIMARY KEY,
    customer_name VARCHAR(255),
    region        VARCHAR(64)
);
INSERT INTO sales.customers (customer_code, customer_name, region)
SELECT 'CUST-' || lp, 'Customer ' || lp, (ARRAY['APAC','EMEA','AMER'])[1 + lp % 3] FROM generate_series(1, 30) AS lp;

DROP TABLE IF EXISTS sales.orders CASCADE;
CREATE TABLE sales.orders (
    sales_order  VARCHAR(32) PRIMARY KEY,
    customer_code VARCHAR(32) REFERENCES sales.customers(customer_code),
    order_ts     TIMESTAMPTZ DEFAULT now(),
    total_value  NUMERIC(12,2),
    status       VARCHAR(32)
);
INSERT INTO sales.orders (sales_order, customer_code, order_ts, total_value, status)
SELECT
    'SO-' || lp,
    'CUST-' || (1 + lp % 30),
    now() - (interval '1 day' * (lp % 90)),
    round((random() * 250000)::numeric, 2),
    (ARRAY['open','shipped','invoiced','closed'])[1 + lp % 4]
FROM generate_series(1, 250) AS lp;

DROP TABLE IF EXISTS sales.order_lines CASCADE;
CREATE TABLE sales.order_lines (
    line_id      BIGSERIAL PRIMARY KEY,
    sales_order  VARCHAR(32) REFERENCES sales.orders(sales_order),
    product_code VARCHAR(32) REFERENCES products.products(product_code),
    quantity     INTEGER,
    unit_price   NUMERIC(12,2)
);
INSERT INTO sales.order_lines (sales_order, product_code, quantity, unit_price)
SELECT 'SO-' || (1 + lp % 250), (SELECT product_code FROM products.products ORDER BY random() LIMIT 1),
       10 + (random() * 500)::int, round((50 + random() * 400)::numeric, 2)
FROM generate_series(1, 1200) AS lp;

-- ---------------------------------------------------------------------------
-- HR
-- ---------------------------------------------------------------------------
DROP TABLE IF EXISTS hr.employees CASCADE;
CREATE TABLE hr.employees (
    employee_no  VARCHAR(32) PRIMARY KEY,
    full_name    VARCHAR(255),
    department   VARCHAR(64),
    salary       NUMERIC(12,2),
    joined_on    DATE
);
INSERT INTO hr.employees (employee_no, full_name, department, salary, joined_on)
SELECT 'EMP-' || lp, 'Employee ' || lp, (ARRAY['production','quality','maintenance','logistics'])[1 + lp % 4],
       round((20000 + random() * 80000)::numeric, 2), current_date - (lp * 30)
FROM generate_series(1, 60) AS lp;

DROP TABLE IF EXISTS hr.attendance CASCADE;
CREATE TABLE hr.attendance (
    attendance_id BIGSERIAL PRIMARY KEY,
    employee_no   VARCHAR(32) REFERENCES hr.employees(employee_no),
    work_date     DATE,
    hours         NUMERIC(5,2)
);
INSERT INTO hr.attendance (employee_no, work_date, hours)
SELECT 'EMP-' || (1 + lp % 60), current_date - (lp % 30), round((6 + random() * 3)::numeric, 2)
FROM generate_series(1, 1500) AS lp;

-- ---------------------------------------------------------------------------
-- LOGISTICS
-- ---------------------------------------------------------------------------
DROP TABLE IF EXISTS logistics.shipments CASCADE;
CREATE TABLE logistics.shipments (
    shipment_no   VARCHAR(32) PRIMARY KEY,
    sales_order   VARCHAR(32),
    carrier       VARCHAR(64),
    shipped_on    TIMESTAMPTZ,
    delivered_on  TIMESTAMPTZ,
    status        VARCHAR(32)
);
INSERT INTO logistics.shipments (shipment_no, sales_order, carrier, shipped_on, delivered_on, status)
SELECT
    'SHP-' || lp,
    'SO-' || (1 + lp % 250),
    (ARRAY['BlueDart','Delhivery','DHL','FedEx'])[1 + lp % 4],
    now() - (interval '1 day' * (lp % 40)),
    now() - (interval '1 day' * (lp % 35)),
    (ARRAY['in_transit','delivered','delivered'])[1 + lp % 3]
FROM generate_series(1, 350) AS lp;