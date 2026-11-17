from alembic import context

from takeaway_insight.models import Base

context.configure(
    connection=context.config.attributes["connection"],
    target_metadata=Base.metadata,
    compare_type=True,
    render_as_batch=True,
)
with context.begin_transaction():
    context.run_migrations()
