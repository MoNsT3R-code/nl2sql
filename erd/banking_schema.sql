-- =============================================================================================
-- Banking Management System: database schema (PostgreSQL 13 or later)
--
-- 20 tables, 20 primary keys, 50 foreign keys, matching the ERD exactly.
-- Sized for about 500,000 customers and the accounts, cards and transactions they generate:
--   * every primary key is a BIGINT identity column, so ids are unique and assigned by the database
--   * every real-world identifier (CNIC, CIF number, IBAN, email, card and loan numbers, ...) has a
--     UNIQUE constraint, so the same person or account can never be loaded twice
--   * CHECK constraints keep codes, formats and amounts valid
--   * every foreign key column is indexed (PostgreSQL does not do this automatically), so joins
--     and deletes stay fast at this size
--   * foreign keys use the default ON DELETE NO ACTION: banking records are never removed by cascade
--
-- Run it with:  psql -d bank -v ON_ERROR_STOP=1 -f banking_schema.sql
-- Everything runs in one transaction, so a failure leaves the database unchanged.
-- =============================================================================================

BEGIN;

-- ---------------------------------------------------------------------------------------------
-- Reference (lookup) tables: no foreign keys
-- ---------------------------------------------------------------------------------------------

CREATE TABLE designation (
    designation_id      BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    title               VARCHAR(60)   NOT NULL,
    grade               VARCHAR(10)   NOT NULL,
    department          VARCHAR(40)   NOT NULL,
    min_salary          DECIMAL(12,2) NOT NULL,
    max_salary          DECIMAL(12,2) NOT NULL,
    approval_limit_pkr  DECIMAL(14,2) NOT NULL DEFAULT 0,
    can_approve_loans   BOOLEAN       NOT NULL DEFAULT FALSE,
    can_authorise_txn   BOOLEAN       NOT NULL DEFAULT FALSE,
    description         VARCHAR(200),
    status              VARCHAR(12)   NOT NULL DEFAULT 'active',
    CONSTRAINT uq_designation_title UNIQUE (title),
    CONSTRAINT ck_designation_salary CHECK (min_salary >= 0 AND max_salary >= min_salary),
    CONSTRAINT ck_designation_limit CHECK (approval_limit_pkr >= 0),
    CONSTRAINT ck_designation_status CHECK (status IN ('active', 'retired'))
);

CREATE TABLE customer_segment (
    segment_id                BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    segment_code              VARCHAR(10)   NOT NULL,
    segment_name              VARCHAR(40)   NOT NULL,
    min_age                   SMALLINT,
    max_age                   SMALLINT,
    min_monthly_income        DECIMAL(12,2) NOT NULL DEFAULT 0,
    min_relationship_balance  DECIMAL(14,2) NOT NULL DEFAULT 0,
    priority_service          BOOLEAN       NOT NULL DEFAULT FALSE,
    fee_waiver_pct            DECIMAL(5,2)  NOT NULL DEFAULT 0,
    description               VARCHAR(200),
    status                    VARCHAR(12)   NOT NULL DEFAULT 'active',
    CONSTRAINT uq_customer_segment_code UNIQUE (segment_code),
    CONSTRAINT uq_customer_segment_name UNIQUE (segment_name),
    CONSTRAINT ck_customer_segment_age CHECK (min_age IS NULL OR max_age IS NULL OR max_age >= min_age),
    CONSTRAINT ck_customer_segment_waiver CHECK (fee_waiver_pct BETWEEN 0 AND 100),
    CONSTRAINT ck_customer_segment_status CHECK (status IN ('active', 'retired'))
);

CREATE TABLE currency (
    currency_id       BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    iso_code          CHAR(3)       NOT NULL,
    currency_name     VARCHAR(40)   NOT NULL,
    symbol            VARCHAR(5)    NOT NULL,
    country           VARCHAR(40)   NOT NULL,
    decimal_places    SMALLINT      NOT NULL DEFAULT 2,
    buy_rate_pkr      DECIMAL(12,4) NOT NULL,
    sell_rate_pkr     DECIMAL(12,4) NOT NULL,
    is_base_currency  BOOLEAN       NOT NULL DEFAULT FALSE,
    rate_updated_at   TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    status            VARCHAR(12)   NOT NULL DEFAULT 'active',
    CONSTRAINT uq_currency_iso_code UNIQUE (iso_code),
    CONSTRAINT ck_currency_iso_code CHECK (iso_code ~ '^[A-Z]{3}$'),
    CONSTRAINT ck_currency_decimals CHECK (decimal_places BETWEEN 0 AND 4),
    CONSTRAINT ck_currency_rates CHECK (buy_rate_pkr > 0 AND sell_rate_pkr >= buy_rate_pkr),
    CONSTRAINT ck_currency_status CHECK (status IN ('active', 'suspended'))
);
-- Only one currency (PKR) can be the base currency.
CREATE UNIQUE INDEX uq_currency_single_base ON currency (is_base_currency) WHERE is_base_currency;

CREATE TABLE card_type (
    card_type_id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    type_code               VARCHAR(10)  NOT NULL,
    type_name               VARCHAR(40)  NOT NULL,
    is_credit               BOOLEAN      NOT NULL,
    is_physical             BOOLEAN      NOT NULL DEFAULT TRUE,
    is_reloadable           BOOLEAN      NOT NULL DEFAULT FALSE,
    default_validity_years  SMALLINT     NOT NULL DEFAULT 5,
    chip_enabled            BOOLEAN      NOT NULL DEFAULT TRUE,
    contactless_enabled     BOOLEAN      NOT NULL DEFAULT TRUE,
    description             VARCHAR(200),
    status                  VARCHAR(12)  NOT NULL DEFAULT 'active',
    CONSTRAINT uq_card_type_code UNIQUE (type_code),
    CONSTRAINT uq_card_type_name UNIQUE (type_name),
    CONSTRAINT ck_card_type_validity CHECK (default_validity_years BETWEEN 1 AND 10),
    CONSTRAINT ck_card_type_status CHECK (status IN ('active', 'retired'))
);

CREATE TABLE channel (
    channel_id      BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    channel_code    VARCHAR(10)   NOT NULL,
    channel_name    VARCHAR(40)   NOT NULL,
    operator        VARCHAR(40)   NOT NULL,
    is_digital      BOOLEAN       NOT NULL,
    available_24x7  BOOLEAN       NOT NULL DEFAULT TRUE,
    per_txn_limit   DECIMAL(12,2) NOT NULL,
    daily_limit     DECIMAL(14,2) NOT NULL,
    requires_otp    BOOLEAN       NOT NULL DEFAULT FALSE,
    fee_per_txn     DECIMAL(8,2)  NOT NULL DEFAULT 0,
    status          VARCHAR(12)   NOT NULL DEFAULT 'active',
    CONSTRAINT uq_channel_code UNIQUE (channel_code),
    CONSTRAINT uq_channel_name UNIQUE (channel_name),
    CONSTRAINT ck_channel_limits CHECK (per_txn_limit > 0 AND daily_limit >= per_txn_limit),
    CONSTRAINT ck_channel_fee CHECK (fee_per_txn >= 0),
    CONSTRAINT ck_channel_status CHECK (status IN ('active', 'maintenance', 'retired'))
);

CREATE TABLE transaction_type (
    txn_type_id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    type_code               VARCHAR(10)   NOT NULL,
    type_name               VARCHAR(40)   NOT NULL,
    direction               VARCHAR(6)    NOT NULL,
    is_reversible           BOOLEAN       NOT NULL DEFAULT TRUE,
    requires_authorisation  BOOLEAN       NOT NULL DEFAULT FALSE,
    service_charge          DECIMAL(8,2)  NOT NULL DEFAULT 0,
    withholding_tax_pct     DECIMAL(5,2)  NOT NULL DEFAULT 0,
    fed_pct                 DECIMAL(5,2)  NOT NULL DEFAULT 0,
    daily_limit             DECIMAL(14,2),
    status                  VARCHAR(12)   NOT NULL DEFAULT 'active',
    CONSTRAINT uq_transaction_type_code UNIQUE (type_code),
    CONSTRAINT uq_transaction_type_name UNIQUE (type_name),
    CONSTRAINT ck_transaction_type_direction CHECK (direction IN ('debit', 'credit')),
    CONSTRAINT ck_transaction_type_charges CHECK (service_charge >= 0
        AND withholding_tax_pct BETWEEN 0 AND 100 AND fed_pct BETWEEN 0 AND 100),
    CONSTRAINT ck_transaction_type_status CHECK (status IN ('active', 'retired'))
);

CREATE TABLE loan_product (
    loan_product_id        BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    product_code           VARCHAR(10)   NOT NULL,
    product_name           VARCHAR(60)   NOT NULL,
    category               VARCHAR(20)   NOT NULL,
    is_islamic             BOOLEAN       NOT NULL DEFAULT FALSE,
    min_amount             DECIMAL(14,2) NOT NULL,
    max_amount             DECIMAL(14,2) NOT NULL,
    max_tenure_months      SMALLINT      NOT NULL,
    rate_type              VARCHAR(12)   NOT NULL,
    spread_over_kibor_pct  DECIMAL(5,2)  NOT NULL DEFAULT 0,
    processing_fee_pct     DECIMAL(5,2)  NOT NULL DEFAULT 0,
    CONSTRAINT uq_loan_product_code UNIQUE (product_code),
    CONSTRAINT uq_loan_product_name UNIQUE (product_name),
    CONSTRAINT ck_loan_product_category CHECK (category IN
        ('personal', 'auto', 'housing', 'sme', 'agriculture', 'gold', 'student', 'green', 'government_scheme')),
    CONSTRAINT ck_loan_product_amounts CHECK (min_amount > 0 AND max_amount >= min_amount),
    CONSTRAINT ck_loan_product_tenure CHECK (max_tenure_months BETWEEN 1 AND 360),
    CONSTRAINT ck_loan_product_rate_type CHECK (rate_type IN ('fixed', 'kibor_linked')),
    CONSTRAINT ck_loan_product_pcts CHECK (spread_over_kibor_pct >= 0 AND processing_fee_pct BETWEEN 0 AND 10)
);

-- ---------------------------------------------------------------------------------------------
-- Organisation
-- ---------------------------------------------------------------------------------------------

-- manager_employee_id is added at the end, because branch and employee refer to each other.
CREATE TABLE branch (
    branch_id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    manager_employee_id  BIGINT,
    parent_branch_id     BIGINT,
    branch_code          VARCHAR(10)  NOT NULL,
    branch_name          VARCHAR(80)  NOT NULL,
    city                 VARCHAR(40)  NOT NULL,
    province             VARCHAR(30)  NOT NULL,
    address              VARCHAR(200) NOT NULL,
    phone                VARCHAR(20)  NOT NULL,
    opened_on            DATE         NOT NULL,
    is_islamic           BOOLEAN      NOT NULL DEFAULT FALSE,
    has_lockers          BOOLEAN      NOT NULL DEFAULT FALSE,
    status               VARCHAR(12)  NOT NULL DEFAULT 'active',
    CONSTRAINT uq_branch_code UNIQUE (branch_code),
    CONSTRAINT uq_branch_name UNIQUE (branch_name),
    CONSTRAINT uq_branch_manager UNIQUE (manager_employee_id),  -- one employee manages at most one branch
    CONSTRAINT fk_branch_parent_branch_id FOREIGN KEY (parent_branch_id) REFERENCES branch (branch_id),
    CONSTRAINT ck_branch_not_own_parent CHECK (parent_branch_id <> branch_id),
    CONSTRAINT ck_branch_province CHECK (province IN ('Punjab', 'Sindh', 'Khyber Pakhtunkhwa', 'Balochistan',
        'Islamabad Capital Territory', 'Gilgit-Baltistan', 'Azad Jammu and Kashmir')),
    CONSTRAINT ck_branch_status CHECK (status IN ('active', 'relocated', 'closed'))
);

CREATE TABLE employee (
    employee_id      BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    branch_id        BIGINT,                 -- NULL for head-office staff
    designation_id   BIGINT        NOT NULL,
    manager_id       BIGINT,                 -- NULL for the chief executive
    employee_no      VARCHAR(12)   NOT NULL,
    first_name       VARCHAR(50)   NOT NULL,
    last_name        VARCHAR(50)   NOT NULL,
    cnic             CHAR(15)      NOT NULL,
    gender           CHAR(1)       NOT NULL,
    email            VARCHAR(120)  NOT NULL,
    mobile           VARCHAR(15)   NOT NULL,
    hire_date        DATE          NOT NULL,
    monthly_salary   DECIMAL(12,2) NOT NULL,
    status           VARCHAR(12)   NOT NULL DEFAULT 'active',
    CONSTRAINT uq_employee_no UNIQUE (employee_no),
    CONSTRAINT uq_employee_cnic UNIQUE (cnic),
    CONSTRAINT uq_employee_email UNIQUE (email),
    CONSTRAINT fk_employee_branch_id FOREIGN KEY (branch_id) REFERENCES branch (branch_id),
    CONSTRAINT fk_employee_designation_id FOREIGN KEY (designation_id) REFERENCES designation (designation_id),
    CONSTRAINT fk_employee_manager_id FOREIGN KEY (manager_id) REFERENCES employee (employee_id),
    CONSTRAINT ck_employee_not_own_manager CHECK (manager_id <> employee_id),
    CONSTRAINT ck_employee_cnic CHECK (cnic ~ '^[0-9]{5}-[0-9]{7}-[0-9]$'),
    CONSTRAINT ck_employee_gender CHECK (gender IN ('M', 'F', 'X')),
    CONSTRAINT ck_employee_email CHECK (email LIKE '%_@_%._%'),
    CONSTRAINT ck_employee_mobile CHECK (mobile ~ '^\+?[0-9]{10,13}$'),
    CONSTRAINT ck_employee_salary CHECK (monthly_salary >= 0),
    CONSTRAINT ck_employee_status CHECK (status IN ('active', 'on_leave', 'resigned', 'terminated'))
);

-- ---------------------------------------------------------------------------------------------
-- Customers
-- ---------------------------------------------------------------------------------------------

-- One row per person or business. For 500,000 people this is the central table: cif_number,
-- cnic and email are each UNIQUE, so nobody can be onboarded twice.
CREATE TABLE customer (
    customer_id               BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    segment_id                BIGINT       NOT NULL,
    home_branch_id            BIGINT       NOT NULL,
    relationship_manager_id   BIGINT,
    referred_by_customer_id   BIGINT,
    onboarded_by_employee_id  BIGINT       NOT NULL,
    cif_number                VARCHAR(12)  NOT NULL,
    full_name                 VARCHAR(120) NOT NULL,
    cnic                      CHAR(15),               -- NULL only for businesses (they use an NTN)
    date_of_birth             DATE,                   -- NULL only for businesses
    gender                    CHAR(1),
    mobile                    VARCHAR(15)  NOT NULL,  -- not unique: families often share one number
    email                     VARCHAR(120),
    address                   VARCHAR(200) NOT NULL,
    kyc_status                VARCHAR(12)  NOT NULL DEFAULT 'pending',
    risk_rating               VARCHAR(8)   NOT NULL DEFAULT 'low',
    CONSTRAINT uq_customer_cif_number UNIQUE (cif_number),
    CONSTRAINT uq_customer_cnic UNIQUE (cnic),
    CONSTRAINT uq_customer_email UNIQUE (email),
    CONSTRAINT fk_customer_segment_id FOREIGN KEY (segment_id) REFERENCES customer_segment (segment_id),
    CONSTRAINT fk_customer_home_branch_id FOREIGN KEY (home_branch_id) REFERENCES branch (branch_id),
    CONSTRAINT fk_customer_relationship_manager_id FOREIGN KEY (relationship_manager_id) REFERENCES employee (employee_id),
    CONSTRAINT fk_customer_referred_by_customer_id FOREIGN KEY (referred_by_customer_id) REFERENCES customer (customer_id),
    CONSTRAINT fk_customer_onboarded_by_employee_id FOREIGN KEY (onboarded_by_employee_id) REFERENCES employee (employee_id),
    CONSTRAINT ck_customer_not_self_referred CHECK (referred_by_customer_id <> customer_id),
    CONSTRAINT ck_customer_cif CHECK (cif_number ~ '^[0-9A-Z]{6,12}$'),
    CONSTRAINT ck_customer_cnic CHECK (cnic ~ '^[0-9]{5}-[0-9]{7}-[0-9]$'),
    CONSTRAINT ck_customer_dob CHECK (date_of_birth >= DATE '1900-01-01'),
    CONSTRAINT ck_customer_gender CHECK (gender IN ('M', 'F', 'X')),
    CONSTRAINT ck_customer_mobile CHECK (mobile ~ '^\+?[0-9]{10,13}$'),
    CONSTRAINT ck_customer_email CHECK (email LIKE '%_@_%._%'),
    CONSTRAINT ck_customer_kyc_status CHECK (kyc_status IN ('pending', 'verified', 'rejected', 'expired')),
    CONSTRAINT ck_customer_risk_rating CHECK (risk_rating IN ('low', 'medium', 'high'))
);

CREATE TABLE kyc_document (
    document_id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    customer_id              BIGINT       NOT NULL,
    verified_by_employee_id  BIGINT,
    document_type            VARCHAR(30)  NOT NULL,
    document_number          VARCHAR(30)  NOT NULL,
    issuing_authority        VARCHAR(60)  NOT NULL,
    issue_date               DATE,
    expiry_date              DATE,
    file_reference           VARCHAR(200) NOT NULL,
    verification_status      VARCHAR(12)  NOT NULL DEFAULT 'pending',
    verified_at              TIMESTAMP,
    biometric_verified       BOOLEAN      NOT NULL DEFAULT FALSE,
    remarks                  VARCHAR(200),
    CONSTRAINT uq_kyc_document_number UNIQUE (document_type, document_number),
    CONSTRAINT fk_kyc_document_customer_id FOREIGN KEY (customer_id) REFERENCES customer (customer_id),
    CONSTRAINT fk_kyc_document_verified_by_employee_id FOREIGN KEY (verified_by_employee_id) REFERENCES employee (employee_id),
    CONSTRAINT ck_kyc_document_type CHECK (document_type IN ('CNIC', 'SNIC', 'NICOP', 'POC', 'Passport', 'B-Form',
        'Salary slip', 'Utility bill', 'NTN certificate', 'SECP registration', 'Student ID')),
    CONSTRAINT ck_kyc_document_dates CHECK (expiry_date IS NULL OR issue_date IS NULL OR expiry_date > issue_date),
    CONSTRAINT ck_kyc_document_status CHECK (verification_status IN ('pending', 'verified', 'rejected', 'expired')),
    -- A document can only be marked verified by a named employee, at a recorded time.
    CONSTRAINT ck_kyc_document_verifier CHECK (verification_status <> 'verified'
        OR (verified_by_employee_id IS NOT NULL AND verified_at IS NOT NULL))
);

-- ---------------------------------------------------------------------------------------------
-- Accounts
-- ---------------------------------------------------------------------------------------------

CREATE TABLE account_type (
    account_type_id      BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    segment_id           BIGINT,                 -- NULL means open to every segment
    currency_id          BIGINT        NOT NULL,
    product_code         VARCHAR(10)   NOT NULL,
    product_name         VARCHAR(60)   NOT NULL,
    category             VARCHAR(20)   NOT NULL,
    is_islamic           BOOLEAN       NOT NULL DEFAULT FALSE,
    min_opening_deposit  DECIMAL(12,2) NOT NULL DEFAULT 0,
    min_balance          DECIMAL(12,2) NOT NULL DEFAULT 0,
    max_balance_limit    DECIMAL(14,2),          -- e.g. the Asaan Account cap; NULL means no cap
    profit_rate_pct      DECIMAL(5,2)  NOT NULL DEFAULT 0,
    monthly_service_fee  DECIMAL(8,2)  NOT NULL DEFAULT 0,
    cheque_book_allowed  BOOLEAN       NOT NULL DEFAULT TRUE,
    CONSTRAINT uq_account_type_code UNIQUE (product_code),
    CONSTRAINT uq_account_type_name UNIQUE (product_name),
    CONSTRAINT fk_account_type_segment_id FOREIGN KEY (segment_id) REFERENCES customer_segment (segment_id),
    CONSTRAINT fk_account_type_currency_id FOREIGN KEY (currency_id) REFERENCES currency (currency_id),
    CONSTRAINT ck_account_type_category CHECK (category IN ('current', 'savings', 'basic', 'term_deposit', 'foreign_currency')),
    CONSTRAINT ck_account_type_amounts CHECK (min_opening_deposit >= 0 AND min_balance >= 0
        AND (max_balance_limit IS NULL OR max_balance_limit > min_balance)),
    CONSTRAINT ck_account_type_rates CHECK (profit_rate_pct BETWEEN 0 AND 100 AND monthly_service_fee >= 0)
);

CREATE TABLE account (
    account_id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    customer_id            BIGINT        NOT NULL,
    account_type_id        BIGINT        NOT NULL,
    branch_id              BIGINT        NOT NULL,
    currency_id            BIGINT        NOT NULL,
    opened_by_employee_id  BIGINT,                 -- NULL for accounts opened in the app
    account_number         VARCHAR(16)   NOT NULL,
    iban                   CHAR(24)      NOT NULL,
    account_title          VARCHAR(120)  NOT NULL,
    opened_on              DATE          NOT NULL DEFAULT CURRENT_DATE,
    closed_on              DATE,
    ledger_balance         DECIMAL(16,2) NOT NULL DEFAULT 0,
    available_balance      DECIMAL(16,2) NOT NULL DEFAULT 0,
    raast_alias            VARCHAR(15),            -- mobile number registered with Raast
    is_dormant             BOOLEAN       NOT NULL DEFAULT FALSE,
    status                 VARCHAR(12)   NOT NULL DEFAULT 'active',
    CONSTRAINT uq_account_number UNIQUE (account_number),
    CONSTRAINT uq_account_iban UNIQUE (iban),
    CONSTRAINT uq_account_raast_alias UNIQUE (raast_alias),  -- a Raast alias points to exactly one account
    CONSTRAINT fk_account_customer_id FOREIGN KEY (customer_id) REFERENCES customer (customer_id),
    CONSTRAINT fk_account_account_type_id FOREIGN KEY (account_type_id) REFERENCES account_type (account_type_id),
    CONSTRAINT fk_account_branch_id FOREIGN KEY (branch_id) REFERENCES branch (branch_id),
    CONSTRAINT fk_account_currency_id FOREIGN KEY (currency_id) REFERENCES currency (currency_id),
    CONSTRAINT fk_account_opened_by_employee_id FOREIGN KEY (opened_by_employee_id) REFERENCES employee (employee_id),
    CONSTRAINT ck_account_number CHECK (account_number ~ '^[0-9]{10,16}$'),
    CONSTRAINT ck_account_iban CHECK (iban ~ '^PK[0-9]{2}[A-Z]{4}[0-9]{16}$'),
    CONSTRAINT ck_account_raast_alias CHECK (raast_alias ~ '^03[0-9]{9}$'),
    CONSTRAINT ck_account_dates CHECK (closed_on IS NULL OR closed_on >= opened_on),
    CONSTRAINT ck_account_available CHECK (available_balance <= ledger_balance),
    CONSTRAINT ck_account_status CHECK (status IN ('active', 'dormant', 'frozen', 'closed')),
    CONSTRAINT ck_account_closed CHECK ((status = 'closed') = (closed_on IS NOT NULL))
);

CREATE TABLE account_holder (
    holder_id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    account_id             BIGINT       NOT NULL,
    customer_id            BIGINT       NOT NULL,
    added_by_employee_id   BIGINT,
    holder_role            VARCHAR(30)  NOT NULL,
    ownership_pct          DECIMAL(5,2),
    signing_authority      VARCHAR(30)  NOT NULL DEFAULT 'none',
    is_primary             BOOLEAN      NOT NULL DEFAULT FALSE,
    relationship_to_owner  VARCHAR(30),
    added_on               DATE         NOT NULL DEFAULT CURRENT_DATE,
    removed_on             DATE,
    mandate_reference      VARCHAR(30),
    notify_by_sms          BOOLEAN      NOT NULL DEFAULT TRUE,
    status                 VARCHAR(12)  NOT NULL DEFAULT 'active',
    CONSTRAINT uq_account_holder_role UNIQUE (account_id, customer_id, holder_role),
    CONSTRAINT fk_account_holder_account_id FOREIGN KEY (account_id) REFERENCES account (account_id),
    CONSTRAINT fk_account_holder_customer_id FOREIGN KEY (customer_id) REFERENCES customer (customer_id),
    CONSTRAINT fk_account_holder_added_by_employee_id FOREIGN KEY (added_by_employee_id) REFERENCES employee (employee_id),
    CONSTRAINT ck_account_holder_role CHECK (holder_role IN ('primary', 'joint', 'either_or_survivor', 'nominee', 'guardian',
        'authorised_signatory', 'mandate_holder', 'power_of_attorney', 'beneficial_owner', 'partner')),
    CONSTRAINT ck_account_holder_pct CHECK (ownership_pct IS NULL OR ownership_pct BETWEEN 0 AND 100),
    CONSTRAINT ck_account_holder_signing CHECK (signing_authority IN ('singly', 'jointly', 'either_or_survivor', 'none')),
    CONSTRAINT ck_account_holder_dates CHECK (removed_on IS NULL OR removed_on >= added_on),
    CONSTRAINT ck_account_holder_status CHECK (status IN ('active', 'removed'))
);
-- Each account has exactly one current primary holder.
CREATE UNIQUE INDEX uq_account_holder_one_primary ON account_holder (account_id)
    WHERE is_primary AND removed_on IS NULL;

-- ---------------------------------------------------------------------------------------------
-- Cards
-- ---------------------------------------------------------------------------------------------

-- Member cards: Teens Club, Kids Club, Asaan (PayPak), Raast-linked PayPak, World, Platinum, ...
CREATE TABLE card_product (
    card_product_id       BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    card_type_id          BIGINT        NOT NULL,
    account_type_id       BIGINT,                 -- NULL means any eligible account
    currency_id           BIGINT        NOT NULL,
    segment_id            BIGINT,                 -- NULL means open to every segment
    product_name          VARCHAR(60)   NOT NULL,
    network               VARCHAR(20)   NOT NULL,
    tier                  VARCHAR(20)   NOT NULL,
    annual_fee            DECIMAL(10,2) NOT NULL DEFAULT 0,
    issuance_fee          DECIMAL(10,2) NOT NULL DEFAULT 0,
    daily_atm_limit       DECIMAL(12,2) NOT NULL,
    daily_pos_limit       DECIMAL(12,2) NOT NULL,
    default_credit_limit  DECIMAL(12,2),          -- credit cards only
    reward_rate_pct       DECIMAL(5,2)  NOT NULL DEFAULT 0,
    status                VARCHAR(12)   NOT NULL DEFAULT 'active',
    CONSTRAINT uq_card_product_name UNIQUE (product_name),
    CONSTRAINT fk_card_product_card_type_id FOREIGN KEY (card_type_id) REFERENCES card_type (card_type_id),
    CONSTRAINT fk_card_product_account_type_id FOREIGN KEY (account_type_id) REFERENCES account_type (account_type_id),
    CONSTRAINT fk_card_product_currency_id FOREIGN KEY (currency_id) REFERENCES currency (currency_id),
    CONSTRAINT fk_card_product_segment_id FOREIGN KEY (segment_id) REFERENCES customer_segment (segment_id),
    CONSTRAINT ck_card_product_network CHECK (network IN ('Visa', 'Mastercard', 'PayPak', 'UnionPay')),
    CONSTRAINT ck_card_product_tier CHECK (tier IN ('Kids', 'Teens', 'Basic', 'Classic', 'Gold', 'Titanium',
        'Platinum', 'World', 'Signature', 'Virtual')),
    CONSTRAINT ck_card_product_fees CHECK (annual_fee >= 0 AND issuance_fee >= 0),
    CONSTRAINT ck_card_product_limits CHECK (daily_atm_limit >= 0 AND daily_pos_limit >= 0
        AND (default_credit_limit IS NULL OR default_credit_limit > 0)),
    CONSTRAINT ck_card_product_status CHECK (status IN ('active', 'retired'))
);

CREATE TABLE card (
    card_id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    account_id           BIGINT        NOT NULL,
    card_product_id      BIGINT        NOT NULL,
    customer_id          BIGINT        NOT NULL,   -- the cardholder, who may not be the account owner
    issuing_branch_id    BIGINT        NOT NULL,
    masked_pan           CHAR(19)      NOT NULL,   -- only the masked number is stored, e.g. 4211 **** **** 0193
    name_on_card         VARCHAR(26)   NOT NULL,
    issued_on            DATE          NOT NULL DEFAULT CURRENT_DATE,
    expiry_date          DATE          NOT NULL,
    credit_limit         DECIMAL(12,2),
    outstanding_balance  DECIMAL(12,2) NOT NULL DEFAULT 0,
    pin_status           VARCHAR(12)   NOT NULL DEFAULT 'not_set',
    contactless_enabled  BOOLEAN       NOT NULL DEFAULT TRUE,
    ecommerce_enabled    BOOLEAN       NOT NULL DEFAULT FALSE,
    status               VARCHAR(12)   NOT NULL DEFAULT 'inactive',
    CONSTRAINT fk_card_account_id FOREIGN KEY (account_id) REFERENCES account (account_id),
    CONSTRAINT fk_card_card_product_id FOREIGN KEY (card_product_id) REFERENCES card_product (card_product_id),
    CONSTRAINT fk_card_customer_id FOREIGN KEY (customer_id) REFERENCES customer (customer_id),
    CONSTRAINT fk_card_issuing_branch_id FOREIGN KEY (issuing_branch_id) REFERENCES branch (branch_id),
    CONSTRAINT ck_card_masked_pan CHECK (masked_pan ~ '^[0-9]{4} \*{4} \*{4} [0-9]{4}$'),
    CONSTRAINT ck_card_dates CHECK (expiry_date > issued_on),
    CONSTRAINT ck_card_amounts CHECK ((credit_limit IS NULL OR credit_limit > 0) AND outstanding_balance >= 0),
    CONSTRAINT ck_card_pin_status CHECK (pin_status IN ('not_set', 'set', 'locked')),
    CONSTRAINT ck_card_status CHECK (status IN ('inactive', 'active', 'blocked', 'hot_listed', 'expired'))
);
-- A cardholder can have only one active card of each product on an account.
CREATE UNIQUE INDEX uq_card_one_active ON card (account_id, customer_id, card_product_id)
    WHERE status = 'active';

-- ---------------------------------------------------------------------------------------------
-- Payments
-- ---------------------------------------------------------------------------------------------

CREATE TABLE merchant (
    merchant_id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    settlement_account_id  BIGINT       NOT NULL,
    acquiring_branch_id    BIGINT       NOT NULL,
    merchant_code          VARCHAR(15)  NOT NULL,
    merchant_name          VARCHAR(80)  NOT NULL,
    mcc                    CHAR(4)      NOT NULL,   -- ISO 18245 merchant category code
    category               VARCHAR(30)  NOT NULL,
    city                   VARCHAR(40)  NOT NULL,
    terminal_count         SMALLINT     NOT NULL DEFAULT 1,
    accepts_raast_qr       BOOLEAN      NOT NULL DEFAULT FALSE,
    mdr_pct                DECIMAL(5,2) NOT NULL,   -- merchant discount rate
    onboarded_on           DATE         NOT NULL DEFAULT CURRENT_DATE,
    status                 VARCHAR(12)  NOT NULL DEFAULT 'active',
    CONSTRAINT uq_merchant_code UNIQUE (merchant_code),
    CONSTRAINT fk_merchant_settlement_account_id FOREIGN KEY (settlement_account_id) REFERENCES account (account_id),
    CONSTRAINT fk_merchant_acquiring_branch_id FOREIGN KEY (acquiring_branch_id) REFERENCES branch (branch_id),
    CONSTRAINT ck_merchant_mcc CHECK (mcc ~ '^[0-9]{4}$'),
    CONSTRAINT ck_merchant_terminals CHECK (terminal_count >= 0),
    CONSTRAINT ck_merchant_mdr CHECK (mdr_pct BETWEEN 0 AND 10),
    CONSTRAINT ck_merchant_status CHECK (status IN ('active', 'suspended', 'terminated'))
);

CREATE TABLE beneficiary (
    beneficiary_id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    customer_id             BIGINT       NOT NULL,   -- who saved this payee
    beneficiary_account_id  BIGINT,                  -- set when the payee also banks with us
    nickname                VARCHAR(40)  NOT NULL,
    beneficiary_name        VARCHAR(120) NOT NULL,
    bank_name               VARCHAR(60)  NOT NULL,
    iban                    CHAR(24),
    raast_id                VARCHAR(15),
    beneficiary_type        VARCHAR(15)  NOT NULL,
    mobile                  VARCHAR(15),
    added_on                DATE         NOT NULL DEFAULT CURRENT_DATE,
    cooling_off_until       TIMESTAMP,
    status                  VARCHAR(12)  NOT NULL DEFAULT 'active',
    CONSTRAINT uq_beneficiary_nickname UNIQUE (customer_id, nickname),
    CONSTRAINT uq_beneficiary_iban UNIQUE (customer_id, iban),
    CONSTRAINT uq_beneficiary_raast_id UNIQUE (customer_id, raast_id),
    CONSTRAINT fk_beneficiary_customer_id FOREIGN KEY (customer_id) REFERENCES customer (customer_id),
    CONSTRAINT fk_beneficiary_beneficiary_account_id FOREIGN KEY (beneficiary_account_id) REFERENCES account (account_id),
    CONSTRAINT ck_beneficiary_type CHECK (beneficiary_type IN ('raast', 'ibft', 'in_house', 'utility')),
    CONSTRAINT ck_beneficiary_iban CHECK (iban ~ '^PK[0-9]{2}[A-Z]{4}[0-9]{16}$'),
    CONSTRAINT ck_beneficiary_raast_id CHECK (raast_id ~ '^03[0-9]{9}$'),
    -- The payee must be reachable somehow.
    CONSTRAINT ck_beneficiary_reachable CHECK (iban IS NOT NULL OR raast_id IS NOT NULL OR beneficiary_account_id IS NOT NULL),
    CONSTRAINT ck_beneficiary_status CHECK (status IN ('active', 'cooling_off', 'disabled'))
);

-- ---------------------------------------------------------------------------------------------
-- Lending
-- ---------------------------------------------------------------------------------------------

CREATE TABLE loan (
    loan_id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    customer_id              BIGINT        NOT NULL,
    loan_product_id          BIGINT        NOT NULL,
    branch_id                BIGINT        NOT NULL,
    loan_officer_id          BIGINT        NOT NULL,
    approved_by_employee_id  BIGINT,                 -- NULL until approved
    disbursement_account_id  BIGINT        NOT NULL,
    loan_number              VARCHAR(15)   NOT NULL,
    principal_amount         DECIMAL(14,2) NOT NULL,
    interest_rate_pct        DECIMAL(5,2)  NOT NULL,
    tenure_months            SMALLINT      NOT NULL,
    emi_amount               DECIMAL(12,2) NOT NULL,
    disbursed_on             DATE,
    maturity_date            DATE,
    outstanding_principal    DECIMAL(14,2) NOT NULL,
    days_past_due            SMALLINT      NOT NULL DEFAULT 0,
    status                   VARCHAR(12)   NOT NULL DEFAULT 'applied',
    CONSTRAINT uq_loan_number UNIQUE (loan_number),
    CONSTRAINT fk_loan_customer_id FOREIGN KEY (customer_id) REFERENCES customer (customer_id),
    CONSTRAINT fk_loan_loan_product_id FOREIGN KEY (loan_product_id) REFERENCES loan_product (loan_product_id),
    CONSTRAINT fk_loan_branch_id FOREIGN KEY (branch_id) REFERENCES branch (branch_id),
    CONSTRAINT fk_loan_loan_officer_id FOREIGN KEY (loan_officer_id) REFERENCES employee (employee_id),
    CONSTRAINT fk_loan_approved_by_employee_id FOREIGN KEY (approved_by_employee_id) REFERENCES employee (employee_id),
    CONSTRAINT fk_loan_disbursement_account_id FOREIGN KEY (disbursement_account_id) REFERENCES account (account_id),
    -- Four-eyes rule: the officer who handles a loan cannot also approve it.
    CONSTRAINT ck_loan_four_eyes CHECK (approved_by_employee_id <> loan_officer_id),
    CONSTRAINT ck_loan_amounts CHECK (principal_amount > 0 AND emi_amount > 0
        AND outstanding_principal BETWEEN 0 AND principal_amount),
    CONSTRAINT ck_loan_rate CHECK (interest_rate_pct BETWEEN 0 AND 60),
    CONSTRAINT ck_loan_tenure CHECK (tenure_months BETWEEN 1 AND 360),
    CONSTRAINT ck_loan_dates CHECK (maturity_date IS NULL OR disbursed_on IS NULL OR maturity_date > disbursed_on),
    CONSTRAINT ck_loan_dpd CHECK (days_past_due >= 0),
    CONSTRAINT ck_loan_status CHECK (status IN ('applied', 'approved', 'rejected', 'active', 'overdue',
        'closed', 'written_off')),
    CONSTRAINT ck_loan_approval CHECK (status IN ('applied', 'rejected') OR approved_by_employee_id IS NOT NULL)
);

-- ---------------------------------------------------------------------------------------------
-- Transactions: the largest table (millions of rows for 500,000 customers)
-- ---------------------------------------------------------------------------------------------

CREATE TABLE bank_transaction (
    transaction_id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    account_id                BIGINT        NOT NULL,
    txn_type_id               BIGINT        NOT NULL,
    channel_id                BIGINT        NOT NULL,
    currency_id               BIGINT        NOT NULL,
    card_id                   BIGINT,       -- card payments and ATM withdrawals
    merchant_id               BIGINT,       -- purchases
    beneficiary_id            BIGINT,       -- transfers to a saved payee
    counterparty_account_id   BIGINT,       -- the other account in an in-house transfer
    loan_id                   BIGINT,       -- loan disbursements and instalments
    processed_by_employee_id  BIGINT,       -- branch transactions only
    reference_no              VARCHAR(20)   NOT NULL,
    rrn                       CHAR(12),     -- retrieval reference number from the card or Raast network
    amount                    DECIMAL(16,2) NOT NULL,   -- always positive; transaction_type.direction gives the sign
    fee_amount                DECIMAL(10,2) NOT NULL DEFAULT 0,
    tax_amount                DECIMAL(10,2) NOT NULL DEFAULT 0,
    balance_after             DECIMAL(16,2) NOT NULL,
    txn_datetime              TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    value_date                DATE          NOT NULL DEFAULT CURRENT_DATE,
    narration                 VARCHAR(140)  NOT NULL,
    status                    VARCHAR(12)   NOT NULL DEFAULT 'posted',
    CONSTRAINT uq_bank_transaction_reference UNIQUE (reference_no),
    CONSTRAINT fk_bank_transaction_account_id FOREIGN KEY (account_id) REFERENCES account (account_id),
    CONSTRAINT fk_bank_transaction_txn_type_id FOREIGN KEY (txn_type_id) REFERENCES transaction_type (txn_type_id),
    CONSTRAINT fk_bank_transaction_channel_id FOREIGN KEY (channel_id) REFERENCES channel (channel_id),
    CONSTRAINT fk_bank_transaction_currency_id FOREIGN KEY (currency_id) REFERENCES currency (currency_id),
    CONSTRAINT fk_bank_transaction_card_id FOREIGN KEY (card_id) REFERENCES card (card_id),
    CONSTRAINT fk_bank_transaction_merchant_id FOREIGN KEY (merchant_id) REFERENCES merchant (merchant_id),
    CONSTRAINT fk_bank_transaction_beneficiary_id FOREIGN KEY (beneficiary_id) REFERENCES beneficiary (beneficiary_id),
    CONSTRAINT fk_bank_transaction_counterparty_account_id FOREIGN KEY (counterparty_account_id) REFERENCES account (account_id),
    CONSTRAINT fk_bank_transaction_loan_id FOREIGN KEY (loan_id) REFERENCES loan (loan_id),
    CONSTRAINT fk_bank_transaction_processed_by_employee_id FOREIGN KEY (processed_by_employee_id) REFERENCES employee (employee_id),
    CONSTRAINT ck_bank_transaction_amounts CHECK (amount > 0 AND fee_amount >= 0 AND tax_amount >= 0),
    CONSTRAINT ck_bank_transaction_counterparty CHECK (counterparty_account_id <> account_id),
    CONSTRAINT ck_bank_transaction_status CHECK (status IN ('pending', 'posted', 'reversed', 'failed'))
);

-- ---------------------------------------------------------------------------------------------
-- The circular foreign key: each branch's manager is an employee (who works at a branch).
-- ---------------------------------------------------------------------------------------------

ALTER TABLE branch ADD CONSTRAINT fk_branch_manager_employee_id
    FOREIGN KEY (manager_employee_id) REFERENCES employee (employee_id);

-- ---------------------------------------------------------------------------------------------
-- Indexes on foreign key columns (columns already covered by a UNIQUE constraint above are left
-- out: branch.manager_employee_id, kyc/account_holder/beneficiary lookups by leading column).
-- ---------------------------------------------------------------------------------------------

CREATE INDEX ix_branch_parent_branch_id            ON branch (parent_branch_id);
CREATE INDEX ix_employee_branch_id                 ON employee (branch_id);
CREATE INDEX ix_employee_designation_id            ON employee (designation_id);
CREATE INDEX ix_employee_manager_id                ON employee (manager_id);
CREATE INDEX ix_customer_segment_id                ON customer (segment_id);
CREATE INDEX ix_customer_home_branch_id            ON customer (home_branch_id);
CREATE INDEX ix_customer_relationship_manager_id   ON customer (relationship_manager_id);
CREATE INDEX ix_customer_referred_by_customer_id   ON customer (referred_by_customer_id);
CREATE INDEX ix_customer_onboarded_by_employee_id  ON customer (onboarded_by_employee_id);
CREATE INDEX ix_kyc_document_customer_id           ON kyc_document (customer_id);
CREATE INDEX ix_kyc_document_verified_by           ON kyc_document (verified_by_employee_id);
CREATE INDEX ix_account_type_segment_id            ON account_type (segment_id);
CREATE INDEX ix_account_type_currency_id           ON account_type (currency_id);
CREATE INDEX ix_account_customer_id                ON account (customer_id);
CREATE INDEX ix_account_account_type_id            ON account (account_type_id);
CREATE INDEX ix_account_branch_id                  ON account (branch_id);
CREATE INDEX ix_account_currency_id                ON account (currency_id);
CREATE INDEX ix_account_opened_by_employee_id      ON account (opened_by_employee_id);
CREATE INDEX ix_account_holder_customer_id         ON account_holder (customer_id);
CREATE INDEX ix_account_holder_added_by            ON account_holder (added_by_employee_id);
CREATE INDEX ix_card_product_card_type_id          ON card_product (card_type_id);
CREATE INDEX ix_card_product_account_type_id       ON card_product (account_type_id);
CREATE INDEX ix_card_product_currency_id           ON card_product (currency_id);
CREATE INDEX ix_card_product_segment_id            ON card_product (segment_id);
CREATE INDEX ix_card_account_id                    ON card (account_id);
CREATE INDEX ix_card_card_product_id               ON card (card_product_id);
CREATE INDEX ix_card_customer_id                   ON card (customer_id);
CREATE INDEX ix_card_issuing_branch_id             ON card (issuing_branch_id);
CREATE INDEX ix_merchant_settlement_account_id     ON merchant (settlement_account_id);
CREATE INDEX ix_merchant_acquiring_branch_id       ON merchant (acquiring_branch_id);
CREATE INDEX ix_beneficiary_account_id             ON beneficiary (beneficiary_account_id);
CREATE INDEX ix_loan_customer_id                   ON loan (customer_id);
CREATE INDEX ix_loan_loan_product_id               ON loan (loan_product_id);
CREATE INDEX ix_loan_branch_id                     ON loan (branch_id);
CREATE INDEX ix_loan_loan_officer_id               ON loan (loan_officer_id);
CREATE INDEX ix_loan_approved_by_employee_id       ON loan (approved_by_employee_id);
CREATE INDEX ix_loan_disbursement_account_id       ON loan (disbursement_account_id);
CREATE INDEX ix_bank_transaction_account_time      ON bank_transaction (account_id, txn_datetime DESC);  -- statements
CREATE INDEX ix_bank_transaction_txn_type_id       ON bank_transaction (txn_type_id);
CREATE INDEX ix_bank_transaction_channel_id        ON bank_transaction (channel_id);
CREATE INDEX ix_bank_transaction_currency_id       ON bank_transaction (currency_id);
CREATE INDEX ix_bank_transaction_card_id           ON bank_transaction (card_id) WHERE card_id IS NOT NULL;
CREATE INDEX ix_bank_transaction_merchant_id       ON bank_transaction (merchant_id) WHERE merchant_id IS NOT NULL;
CREATE INDEX ix_bank_transaction_beneficiary_id    ON bank_transaction (beneficiary_id) WHERE beneficiary_id IS NOT NULL;
CREATE INDEX ix_bank_transaction_counterparty      ON bank_transaction (counterparty_account_id) WHERE counterparty_account_id IS NOT NULL;
CREATE INDEX ix_bank_transaction_loan_id           ON bank_transaction (loan_id) WHERE loan_id IS NOT NULL;
CREATE INDEX ix_bank_transaction_processed_by      ON bank_transaction (processed_by_employee_id) WHERE processed_by_employee_id IS NOT NULL;

-- Everyday lookups by people's details.
CREATE INDEX ix_customer_mobile    ON customer (mobile);
CREATE INDEX ix_customer_full_name ON customer (lower(full_name));

COMMIT;
