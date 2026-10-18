"""create initial schema

Revision ID: e157ffa10ed3
Revises:
Create Date: 2026-10-18 17:40:07.396894

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "e157ffa10ed3"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Frozen copy of ISO 3166-1 alpha-2 (pycountry 26.2.16). Kept inside the migration on
# purpose: a migration must produce the same result forever, even if the source changes.
ISO_3166_COUNTRIES: list[tuple[str, str]] = [
    ("AD", "Andorra"),
    ("AE", "United Arab Emirates"),
    ("AF", "Afghanistan"),
    ("AG", "Antigua and Barbuda"),
    ("AI", "Anguilla"),
    ("AL", "Albania"),
    ("AM", "Armenia"),
    ("AO", "Angola"),
    ("AQ", "Antarctica"),
    ("AR", "Argentina"),
    ("AS", "American Samoa"),
    ("AT", "Austria"),
    ("AU", "Australia"),
    ("AW", "Aruba"),
    ("AX", "Åland Islands"),
    ("AZ", "Azerbaijan"),
    ("BA", "Bosnia and Herzegovina"),
    ("BB", "Barbados"),
    ("BD", "Bangladesh"),
    ("BE", "Belgium"),
    ("BF", "Burkina Faso"),
    ("BG", "Bulgaria"),
    ("BH", "Bahrain"),
    ("BI", "Burundi"),
    ("BJ", "Benin"),
    ("BL", "Saint Barthélemy"),
    ("BM", "Bermuda"),
    ("BN", "Brunei Darussalam"),
    ("BO", "Bolivia, Plurinational State of"),
    ("BQ", "Bonaire, Sint Eustatius and Saba"),
    ("BR", "Brazil"),
    ("BS", "Bahamas"),
    ("BT", "Bhutan"),
    ("BV", "Bouvet Island"),
    ("BW", "Botswana"),
    ("BY", "Belarus"),
    ("BZ", "Belize"),
    ("CA", "Canada"),
    ("CC", "Cocos (Keeling) Islands"),
    ("CD", "Congo, The Democratic Republic of the"),
    ("CF", "Central African Republic"),
    ("CG", "Congo"),
    ("CH", "Switzerland"),
    ("CI", "Côte d'Ivoire"),
    ("CK", "Cook Islands"),
    ("CL", "Chile"),
    ("CM", "Cameroon"),
    ("CN", "China"),
    ("CO", "Colombia"),
    ("CR", "Costa Rica"),
    ("CU", "Cuba"),
    ("CV", "Cabo Verde"),
    ("CW", "Curaçao"),
    ("CX", "Christmas Island"),
    ("CY", "Cyprus"),
    ("CZ", "Czechia"),
    ("DE", "Germany"),
    ("DJ", "Djibouti"),
    ("DK", "Denmark"),
    ("DM", "Dominica"),
    ("DO", "Dominican Republic"),
    ("DZ", "Algeria"),
    ("EC", "Ecuador"),
    ("EE", "Estonia"),
    ("EG", "Egypt"),
    ("EH", "Western Sahara"),
    ("ER", "Eritrea"),
    ("ES", "Spain"),
    ("ET", "Ethiopia"),
    ("FI", "Finland"),
    ("FJ", "Fiji"),
    ("FK", "Falkland Islands (Malvinas)"),
    ("FM", "Micronesia, Federated States of"),
    ("FO", "Faroe Islands"),
    ("FR", "France"),
    ("GA", "Gabon"),
    ("GB", "United Kingdom"),
    ("GD", "Grenada"),
    ("GE", "Georgia"),
    ("GF", "French Guiana"),
    ("GG", "Guernsey"),
    ("GH", "Ghana"),
    ("GI", "Gibraltar"),
    ("GL", "Greenland"),
    ("GM", "Gambia"),
    ("GN", "Guinea"),
    ("GP", "Guadeloupe"),
    ("GQ", "Equatorial Guinea"),
    ("GR", "Greece"),
    ("GS", "South Georgia and the South Sandwich Islands"),
    ("GT", "Guatemala"),
    ("GU", "Guam"),
    ("GW", "Guinea-Bissau"),
    ("GY", "Guyana"),
    ("HK", "Hong Kong"),
    ("HM", "Heard Island and McDonald Islands"),
    ("HN", "Honduras"),
    ("HR", "Croatia"),
    ("HT", "Haiti"),
    ("HU", "Hungary"),
    ("ID", "Indonesia"),
    ("IE", "Ireland"),
    ("IL", "Israel"),
    ("IM", "Isle of Man"),
    ("IN", "India"),
    ("IO", "British Indian Ocean Territory"),
    ("IQ", "Iraq"),
    ("IR", "Iran, Islamic Republic of"),
    ("IS", "Iceland"),
    ("IT", "Italy"),
    ("JE", "Jersey"),
    ("JM", "Jamaica"),
    ("JO", "Jordan"),
    ("JP", "Japan"),
    ("KE", "Kenya"),
    ("KG", "Kyrgyzstan"),
    ("KH", "Cambodia"),
    ("KI", "Kiribati"),
    ("KM", "Comoros"),
    ("KN", "Saint Kitts and Nevis"),
    ("KP", "Korea, Democratic People's Republic of"),
    ("KR", "Korea, Republic of"),
    ("KW", "Kuwait"),
    ("KY", "Cayman Islands"),
    ("KZ", "Kazakhstan"),
    ("LA", "Lao People's Democratic Republic"),
    ("LB", "Lebanon"),
    ("LC", "Saint Lucia"),
    ("LI", "Liechtenstein"),
    ("LK", "Sri Lanka"),
    ("LR", "Liberia"),
    ("LS", "Lesotho"),
    ("LT", "Lithuania"),
    ("LU", "Luxembourg"),
    ("LV", "Latvia"),
    ("LY", "Libya"),
    ("MA", "Morocco"),
    ("MC", "Monaco"),
    ("MD", "Moldova, Republic of"),
    ("ME", "Montenegro"),
    ("MF", "Saint Martin (French part)"),
    ("MG", "Madagascar"),
    ("MH", "Marshall Islands"),
    ("MK", "North Macedonia"),
    ("ML", "Mali"),
    ("MM", "Myanmar"),
    ("MN", "Mongolia"),
    ("MO", "Macao"),
    ("MP", "Northern Mariana Islands"),
    ("MQ", "Martinique"),
    ("MR", "Mauritania"),
    ("MS", "Montserrat"),
    ("MT", "Malta"),
    ("MU", "Mauritius"),
    ("MV", "Maldives"),
    ("MW", "Malawi"),
    ("MX", "Mexico"),
    ("MY", "Malaysia"),
    ("MZ", "Mozambique"),
    ("NA", "Namibia"),
    ("NC", "New Caledonia"),
    ("NE", "Niger"),
    ("NF", "Norfolk Island"),
    ("NG", "Nigeria"),
    ("NI", "Nicaragua"),
    ("NL", "Netherlands"),
    ("NO", "Norway"),
    ("NP", "Nepal"),
    ("NR", "Nauru"),
    ("NU", "Niue"),
    ("NZ", "New Zealand"),
    ("OM", "Oman"),
    ("PA", "Panama"),
    ("PE", "Peru"),
    ("PF", "French Polynesia"),
    ("PG", "Papua New Guinea"),
    ("PH", "Philippines"),
    ("PK", "Pakistan"),
    ("PL", "Poland"),
    ("PM", "Saint Pierre and Miquelon"),
    ("PN", "Pitcairn"),
    ("PR", "Puerto Rico"),
    ("PS", "Palestine, State of"),
    ("PT", "Portugal"),
    ("PW", "Palau"),
    ("PY", "Paraguay"),
    ("QA", "Qatar"),
    ("RE", "Réunion"),
    ("RO", "Romania"),
    ("RS", "Serbia"),
    ("RU", "Russian Federation"),
    ("RW", "Rwanda"),
    ("SA", "Saudi Arabia"),
    ("SB", "Solomon Islands"),
    ("SC", "Seychelles"),
    ("SD", "Sudan"),
    ("SE", "Sweden"),
    ("SG", "Singapore"),
    ("SH", "Saint Helena, Ascension and Tristan da Cunha"),
    ("SI", "Slovenia"),
    ("SJ", "Svalbard and Jan Mayen"),
    ("SK", "Slovakia"),
    ("SL", "Sierra Leone"),
    ("SM", "San Marino"),
    ("SN", "Senegal"),
    ("SO", "Somalia"),
    ("SR", "Suriname"),
    ("SS", "South Sudan"),
    ("ST", "Sao Tome and Principe"),
    ("SV", "El Salvador"),
    ("SX", "Sint Maarten (Dutch part)"),
    ("SY", "Syrian Arab Republic"),
    ("SZ", "Eswatini"),
    ("TC", "Turks and Caicos Islands"),
    ("TD", "Chad"),
    ("TF", "French Southern Territories"),
    ("TG", "Togo"),
    ("TH", "Thailand"),
    ("TJ", "Tajikistan"),
    ("TK", "Tokelau"),
    ("TL", "Timor-Leste"),
    ("TM", "Turkmenistan"),
    ("TN", "Tunisia"),
    ("TO", "Tonga"),
    ("TR", "Türkiye"),
    ("TT", "Trinidad and Tobago"),
    ("TV", "Tuvalu"),
    ("TW", "Taiwan, Province of China"),
    ("TZ", "Tanzania, United Republic of"),
    ("UA", "Ukraine"),
    ("UG", "Uganda"),
    ("UM", "United States Minor Outlying Islands"),
    ("US", "United States"),
    ("UY", "Uruguay"),
    ("UZ", "Uzbekistan"),
    ("VA", "Holy See (Vatican City State)"),
    ("VC", "Saint Vincent and the Grenadines"),
    ("VE", "Venezuela, Bolivarian Republic of"),
    ("VG", "Virgin Islands, British"),
    ("VI", "Virgin Islands, U.S."),
    ("VN", "Viet Nam"),
    ("VU", "Vanuatu"),
    ("WF", "Wallis and Futuna"),
    ("WS", "Samoa"),
    ("YE", "Yemen"),
    ("YT", "Mayotte"),
    ("ZA", "South Africa"),
    ("ZM", "Zambia"),
    ("ZW", "Zimbabwe"),
]

# updated_at must mean "the content changed". The collector refreshes last_seen_at on
# every sighting, so an update that touches nothing but last_seen_at keeps updated_at.
CREATE_SET_UPDATED_AT_FUNCTION = """
CREATE FUNCTION notices_set_updated_at() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF (to_jsonb(NEW) - 'last_seen_at' - 'updated_at')
        IS DISTINCT FROM (to_jsonb(OLD) - 'last_seen_at' - 'updated_at') THEN
        NEW.updated_at := now();
    END IF;
    RETURN NEW;
END;
$$
"""

CREATE_SET_UPDATED_AT_TRIGGER = """
CREATE TRIGGER trg_notices_set_updated_at
BEFORE UPDATE ON notices
FOR EACH ROW EXECUTE FUNCTION notices_set_updated_at()
"""


def upgrade() -> None:
    """Upgrade schema."""
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table(
        "countries",
        sa.Column("code", sa.CHAR(length=2), nullable=False),
        sa.Column("name", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("code", name=op.f("pk_countries")),
    )
    op.create_table(
        "notices",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("entity_id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("forename", sa.Text(), nullable=True),
        sa.Column("sex_id", sa.Text(), nullable=True),
        sa.Column("date_of_birth", sa.Date(), nullable=True),
        sa.Column("place_of_birth", sa.Text(), nullable=True),
        sa.Column("country_of_birth_code", sa.CHAR(length=2), nullable=True),
        sa.Column("height", sa.Numeric(precision=3, scale=2), nullable=True),
        sa.Column("weight", sa.SmallInteger(), nullable=True),
        sa.Column("distinguishing_marks", sa.Text(), nullable=True),
        sa.Column(
            "language_codes", postgresql.ARRAY(sa.Text()), server_default="{}", nullable=False
        ),
        sa.Column(
            "eye_color_codes", postgresql.ARRAY(sa.Text()), server_default="{}", nullable=False
        ),
        sa.Column(
            "hair_color_codes", postgresql.ARRAY(sa.Text()), server_default="{}", nullable=False
        ),
        sa.Column("thumbnail_picture_id", sa.Text(), nullable=True),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("sex_id IN ('M', 'F', 'U')", name=op.f("ck_notices_sex_id_valid")),
        sa.CheckConstraint("height > 0 AND height < 3", name=op.f("ck_notices_height_range")),
        sa.CheckConstraint("weight > 0 AND weight < 500", name=op.f("ck_notices_weight_range")),
        sa.ForeignKeyConstraint(
            ["country_of_birth_code"],
            ["countries.code"],
            name=op.f("fk_notices_country_of_birth_code_countries"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notices")),
        sa.UniqueConstraint("entity_id", name=op.f("uq_notices_entity_id")),
    )
    op.create_index(
        op.f("ix_notices_country_of_birth_code"), "notices", ["country_of_birth_code"], unique=False
    )
    op.create_index(
        op.f("ix_notices_deleted_at"),
        "notices",
        ["deleted_at"],
        unique=False,
        postgresql_where=sa.text("deleted_at IS NOT NULL"),
    )
    op.create_table(
        "arrest_warrants",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("notice_id", sa.BigInteger(), nullable=False),
        sa.Column("issuing_country_code", sa.CHAR(length=2), nullable=False),
        sa.Column("charge", sa.Text(), nullable=False),
        sa.Column("charge_translation", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["issuing_country_code"],
            ["countries.code"],
            name=op.f("fk_arrest_warrants_issuing_country_code_countries"),
        ),
        sa.ForeignKeyConstraint(
            ["notice_id"],
            ["notices.id"],
            name=op.f("fk_arrest_warrants_notice_id_notices"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_arrest_warrants")),
    )
    op.create_index(
        op.f("ix_arrest_warrants_issuing_country_code"),
        "arrest_warrants",
        ["issuing_country_code"],
        unique=False,
    )
    op.create_index(
        op.f("ix_arrest_warrants_notice_id"), "arrest_warrants", ["notice_id"], unique=False
    )
    op.create_table(
        "notice_nationalities",
        sa.Column("notice_id", sa.BigInteger(), nullable=False),
        sa.Column("country_code", sa.CHAR(length=2), nullable=False),
        sa.ForeignKeyConstraint(
            ["country_code"],
            ["countries.code"],
            name=op.f("fk_notice_nationalities_country_code_countries"),
        ),
        sa.ForeignKeyConstraint(
            ["notice_id"],
            ["notices.id"],
            name=op.f("fk_notice_nationalities_notice_id_notices"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("notice_id", "country_code", name=op.f("pk_notice_nationalities")),
    )
    op.create_index(
        op.f("ix_notice_nationalities_country_code"),
        "notice_nationalities",
        ["country_code"],
        unique=False,
    )
    op.create_table(
        "notice_photos",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("notice_id", sa.BigInteger(), nullable=False),
        sa.Column("picture_id", sa.Text(), nullable=False),
        sa.Column("object_key", sa.Text(), nullable=True),
        sa.Column("sha256", sa.Text(), nullable=True),
        sa.Column("downloaded_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["notice_id"],
            ["notices.id"],
            name=op.f("fk_notice_photos_notice_id_notices"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notice_photos")),
        sa.UniqueConstraint("picture_id", name=op.f("uq_notice_photos_picture_id")),
    )
    op.create_index(
        op.f("ix_notice_photos_notice_id"), "notice_photos", ["notice_id"], unique=False
    )
    # ### end Alembic commands ###

    # --- written by hand: autogenerate does not detect triggers or data -------------
    op.execute(CREATE_SET_UPDATED_AT_FUNCTION)
    op.execute(CREATE_SET_UPDATED_AT_TRIGGER)

    # A lightweight table description instead of the Country model: the model may change
    # in later revisions, while this migration must keep seeing the table as it is here.
    countries = sa.table("countries", sa.column("code", sa.CHAR(2)), sa.column("name", sa.Text))
    op.bulk_insert(countries, [{"code": code, "name": name} for code, name in ISO_3166_COUNTRIES])


def downgrade() -> None:
    """Downgrade schema."""
    # --- written by hand: undo what upgrade() added after the generated commands -------
    # The trigger disappears with its table, but the function does not.
    op.execute("DROP TRIGGER trg_notices_set_updated_at ON notices")
    op.execute("DROP FUNCTION notices_set_updated_at()")

    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_index(op.f("ix_notice_photos_notice_id"), table_name="notice_photos")
    op.drop_table("notice_photos")
    op.drop_index(op.f("ix_notice_nationalities_country_code"), table_name="notice_nationalities")
    op.drop_table("notice_nationalities")
    op.drop_index(op.f("ix_arrest_warrants_notice_id"), table_name="arrest_warrants")
    op.drop_index(op.f("ix_arrest_warrants_issuing_country_code"), table_name="arrest_warrants")
    op.drop_table("arrest_warrants")
    op.drop_index(
        op.f("ix_notices_deleted_at"),
        table_name="notices",
        postgresql_where=sa.text("deleted_at IS NOT NULL"),
    )
    op.drop_index(op.f("ix_notices_country_of_birth_code"), table_name="notices")
    op.drop_table("notices")
    op.drop_table("countries")
    # ### end Alembic commands ###
