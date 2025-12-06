"""initial_schema

Revision ID: 0118b4069598
Revises: <ID_GENERATO_DA_ALEMBIC>
Create Date: 2025-12-06 15:08:52.267597

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0118b4069598'
down_revision: Union[str, Sequence[str], None] = '<ID_GENERATO_DA_ALEMBIC>'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
