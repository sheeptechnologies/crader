import click
import os
import logging
from crader.indexer import CodebaseIndexer
# from crader.embedding.provider import OpenAIEmbeddingProvider

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

@click.group()
def cli():
    """Crader - Sheep Codebase Indexer CLI"""
    pass

@cli.command()
@click.argument('repo_url')
@click.option('--branch', default='main', help='Branch to index')
@click.option('--db-url', default=None, help='Database connection string')
@click.option('--force', is_flag=True, help='Force re-indexing')
@click.option('--auto-prune', is_flag=True, help='Auto prune old snapshots')
def index(repo_url, branch, db_url, force, auto_prune):
    """Index a repository."""
    
    # Fallback to env var if db_url not provided
    if not db_url:
        db_url = os.getenv("DB_URL")
        
    if not db_url:
        click.echo("Error: DB_URL must be provided via argument or environment variable.", err=True)
        return

    indexer = CodebaseIndexer(repo_url=repo_url, branch=branch, db_url=db_url)
    try:
        snapshot_id = indexer.index(force=force, auto_prune=auto_prune)
        click.echo(f"Indexing completed. Snapshot ID: {snapshot_id}")
    except Exception as e:
        click.echo(f"Indexing failed: {e}", err=True)
        exit(1)
    finally:
        indexer.close()

if __name__ == '__main__':
    cli()
