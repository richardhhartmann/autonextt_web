import pyodbc
from flask import Flask, render_template, request, redirect, session, flash, jsonify
from datetime import datetime
import json

app = Flask(__name__)
app.secret_key = 'chave_secreta_segura'

# --- Configurações do Banco de Dados ---
DB_SERVER = 'localhost'
DB_DATABASE = 'NexttLoja'

def conectar_sqlserver():
    """Cria e retorna uma conexão com o banco de dados SQL Server."""
    try:
        conn_str = (f'DRIVER={{ODBC Driver 18 for SQL Server}};SERVER={DB_SERVER};DATABASE={DB_DATABASE};Trusted_Connection=yes;TrustServerCertificate=yes;')
        conn = pyodbc.connect(conn_str, autocommit=False)
        return conn
    except pyodbc.Error as ex:
        print(f"Erro de conexão com o banco de dados: {ex}")
        return None

def rows_to_dicts(cursor):
    """Converte pyodbc.Row para uma lista de dicionários."""
    columns = [column[0] for column in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]

def r_form(key, default=None):
    """Helper para obter dados do formulário com um padrão, tratando strings vazias."""
    val = request.form.get(key)
    return val if val not in [None, ''] else default

# --- Rotas de Autenticação e Dashboard ---
@app.route('/', methods=['GET', 'POST'])
def login():
    if 'usuario_logado' in session: return redirect('/dashboard')
    if request.method == 'POST':
        login_usuario, senha = request.form['login'], request.form['senha']
        conn = conectar_sqlserver()
        if conn:
            user = None
            try:
                cur = conn.cursor()
                cur.execute("SELECT usu_codigo, usu_login FROM tb_usuario WHERE usu_login = ? AND usu_senha = ?", (login_usuario, senha))
                user = cur.fetchone()
            except pyodbc.Error as e: flash(f'Erro ao autenticar: {e}', 'danger')
            finally: conn.close()

            if user:
                session['usuario_logado'], session['usuario_codigo'] = user.usu_login, user.usu_codigo
                flash('Login realizado com sucesso!', 'success')
                return redirect('/dashboard')
            else: flash('Login ou senha inválidos.', 'danger')
        else: flash('Não foi possível conectar ao banco de dados.', 'danger')
        return redirect('/')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    flash('Você foi desconectado.', 'info')
    return redirect('/')

@app.route('/dashboard')
def dashboard():
    if 'usuario_logado' not in session: return redirect('/')
    return render_template('dashboard.html')

# --- Rotas de Produtos ---
@app.route('/produtos')
def visualizar_produtos():
    if 'usuario_logado' not in session: return redirect('/')
    colunas = ['Seção', 'Espécie', 'Descrição', 'Marca', 'Referência', 'Cor', 'Tamanho', 'Ativo']
    return render_template('visualizar_produtos.html', colunas=colunas)

@app.route('/api/produtos')
def api_produtos():
    if 'usuario_logado' not in session: return jsonify({"error": "Não autorizado"}), 401
    
    page = request.args.get('page', 1, type=int)
    search_term = request.args.get('search', '', type=str)
    per_page = 100
    offset = (page - 1) * per_page

    conn = conectar_sqlserver()
    produtos = []
    if conn:
        try:
            cursor = conn.cursor()
            base_query = """
                FROM tb_produto p
                LEFT JOIN tb_secao s ON p.sec_codigo = s.sec_codigo
                LEFT JOIN tb_especie e ON p.esp_codigo = e.esp_codigo
                LEFT JOIN tb_marca m ON p.mar_codigo = m.mar_codigo
            """
            where_clause = ""
            params = []
            if search_term:
                like_term = f"%{search_term}%"
                where_clause = "WHERE p.prd_descricao LIKE ? OR m.mar_descricao LIKE ? OR p.prd_referencia_fornec LIKE ?"
                params.extend([like_term, like_term, like_term])

            query = f"""
                SELECT 
                    s.sec_descricao AS 'Seção',
                    e.esp_descricao AS 'Espécie',
                    p.prd_descricao AS 'Descrição',
                    m.mar_descricao AS 'Marca',
                    p.prd_referencia_fornec AS 'Referência',
                    (SELECT STRING_AGG(aip.aip_descricao, ', ') FROM tb_atributo_item_produto aip JOIN tb_tipo_atributo tpa ON aip.tpa_codigo = tpa.tpa_codigo WHERE aip.sec_codigo = p.sec_codigo AND aip.esp_codigo = p.esp_codigo AND aip.prd_codigo = p.prd_codigo AND tpa.tpa_descricao LIKE '%COR%') AS 'Cor',
                    (SELECT STRING_AGG(aip.aip_descricao, ', ') FROM tb_atributo_item_produto aip JOIN tb_tipo_atributo tpa ON aip.tpa_codigo = tpa.tpa_codigo WHERE aip.sec_codigo = p.sec_codigo AND aip.esp_codigo = p.esp_codigo AND aip.prd_codigo = p.prd_codigo AND tpa.tpa_descricao LIKE '%TAMANHO%') AS 'Tamanho',
                    p.prd_ativo AS 'Ativo',
                    p.sec_codigo, p.esp_codigo, p.prd_codigo
                {base_query}
                {where_clause}
                ORDER BY p.prd_descricao
                OFFSET ? ROWS FETCH NEXT ? ROWS ONLY;
            """
            params.extend([offset, per_page])
            cursor.execute(query, params)
            produtos = rows_to_dicts(cursor)
        except pyodbc.Error as e:
            return jsonify({"error": str(e)}), 500
        finally:
            conn.close()
    return jsonify(produtos)


@app.route('/produtos/cadastrar', methods=['GET', 'POST'])
def cadastrar_produto():
    if 'usuario_logado' not in session: return redirect('/')
    if request.method == 'POST':
        return handle_produto_post()
    
    conn = conectar_sqlserver()
    marcas = []
    if conn:
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT mar_codigo, mar_descricao FROM tb_marca ORDER BY mar_descricao")
            marcas = cursor.fetchall()
        except pyodbc.Error as e: flash(f"Erro ao carregar marcas: {e}", "danger")
        finally: conn.close()

    return render_template('produto_form.html', produto=None, itens_grade='[]', marcas=marcas)

@app.route('/produtos/editar/<int:sec_codigo>/<int:esp_codigo>/<int:prd_codigo>', methods=['GET', 'POST'])
def editar_produto(sec_codigo, esp_codigo, prd_codigo):
    if 'usuario_logado' not in session: return redirect('/')
    if request.method == 'POST':
        return handle_produto_post(is_edit=True)

    conn = None
    produto, itens_grade_dict, marcas = None, [], []
    try:
        conn = conectar_sqlserver()
        if conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM tb_produto WHERE sec_codigo=? AND esp_codigo=? AND prd_codigo=?", (sec_codigo, esp_codigo, prd_codigo))
            produto = cursor.fetchone()
            if not produto:
                flash("Produto não encontrado!", "danger")
                return redirect('/produtos')
            
            cursor.execute("SELECT * FROM tb_item_produto WHERE sec_codigo=? AND esp_codigo=? AND prd_codigo=?", (sec_codigo, esp_codigo, prd_codigo))
            itens_grade_dict = rows_to_dicts(cursor)
            
            cursor.execute("SELECT mar_codigo, mar_descricao FROM tb_marca ORDER BY mar_descricao")
            marcas = cursor.fetchall()
        else: flash("Não foi possível conectar ao banco de dados.", "danger")
    except pyodbc.Error as e: flash(f"Erro ao carregar dados do produto: {e}", "danger")
    finally:
        if conn: conn.close()
    
    return render_template('produto_form.html', produto=produto, itens_grade=json.dumps(itens_grade_dict, default=str), marcas=marcas)

@app.route('/produtos/excluir/<int:sec_codigo>/<int:esp_codigo>/<int:prd_codigo>', methods=['POST'])
def excluir_produto(sec_codigo, esp_codigo, prd_codigo):
    if 'usuario_logado' not in session: return redirect('/')
    conn = None
    try:
        conn = conectar_sqlserver()
        if not conn: raise Exception("Não foi possível conectar ao banco de dados.")
        
        cursor = conn.cursor()
        sql = "{CALL pr_produto_d (?, ?, ?)}"
        cursor.execute(sql, (sec_codigo, esp_codigo, prd_codigo))
        conn.commit()
        flash('Produto excluído com sucesso!', 'success')
    except pyodbc.Error as e:
        conn.rollback()
        flash(f'Erro ao excluir produto: {e}', 'danger')
    finally:
        if conn: conn.close()
    return redirect('/produtos')

def handle_produto_post(is_edit=False):
    conn = None
    try:
        conn = conectar_sqlserver()
        if not conn: raise Exception("Não foi possível conectar ao banco de dados.")

        cursor = conn.cursor()
        usu_codigo = session.get('usuario_codigo')
        
        cursor.execute("DELETE FROM aux_item_produto WHERE usu_codigo = ?", usu_codigo)
        cursor.execute("DELETE FROM aux_atributo_item_produto WHERE usu_codigo = ?", usu_codigo)
        cursor.execute("DELETE FROM aux_atributo_produto WHERE usu_codigo = ?", usu_codigo)

        itens_grade = json.loads(request.form.get('itens_grade_json', '[]'))
        if not itens_grade: raise ValueError("O produto deve ter pelo menos um item na grade.")

        p_sec_codigo = int(r_form('sec_codigo'))
        p_esp_codigo = int(r_form('esp_codigo'))
        p_prd_codigo = int(r_form('prd_codigo')) if is_edit else 0

        for item in itens_grade:
            cursor.execute("INSERT INTO aux_item_produto(usu_codigo, sec_codigo, esp_codigo, prd_codigo, ipr_codigo, ipr_codigo_barra, ipr_preco_promocional) VALUES (?, ?, ?, ?, ?, ?, ?)",
                usu_codigo, p_sec_codigo, p_esp_codigo, p_prd_codigo,
                item.get('ipr_codigo'), item.get('ipr_codigo_barra'), item.get('ipr_preco_promocional'))

        if is_edit:
            params = (
                usu_codigo, p_sec_codigo, p_esp_codigo, p_prd_codigo,
                r_form('prd_descricao'), r_form('prd_descricao_reduzida'), int(r_form('mar_codigo', 0)) or None,
                r_form('prd_data_cadastro'), r_form('prd_unidade'), r_form('prd_data_ultima_compra'),
                r_form('prd_data_ultima_entrega'), float(r_form('prd_custo_medio', 0.0)), float(r_form('prd_ultimo_custo', 0.0)),
                float(r_form('prd_preco_medio', 0.0)), float(r_form('prd_aliquota_icms', 0.0)), r_form('prd_codigo_original'),
                1 if 'prd_ativo' in request.form else 0, r_form('prd_arquivo_foto'), r_form('prd_referencia_fornec'),
                r_form('clf_codigo'), int(r_form('usu_codigo_comprador', 0)) or None, int(r_form('und_codigo', 0)) or None,
                float(r_form('prd_valor_venda', 0.0)) or None, r_form('prd_origem'), float(r_form('prd_percentual_icms', 0.0)) or None,
                float(r_form('prd_percentual_ipi', 0.0)) or None, r_form('etq_codigo_padrao'), 1 if 'prd_permite_comprar' in request.form else 0,
                int(r_form('udc_codigo', 0)) or None, float(r_form('prd_valor_unidade_conversao', 0.0)) or None
            )
            sql = "{CALL pr_produto_u (" + "?,"*29 + "?)}"
        else:
            params = (
                usu_codigo, p_sec_codigo, p_esp_codigo, 0,
                r_form('prd_descricao'), r_form('prd_descricao_reduzida'), int(r_form('mar_codigo', 0)) or None,
                datetime.now(), r_form('prd_unidade'), None, None,
                float(r_form('prd_custo_medio', 0.0)), float(r_form('prd_ultimo_custo', 0.0)),
                float(r_form('prd_preco_medio', 0.0)), float(r_form('prd_aliquota_icms', 0.0)), r_form('prd_codigo_original'),
                1 if 'prd_ativo' in request.form else 0, r_form('prd_arquivo_foto'), r_form('prd_referencia_fornec'),
                r_form('clf_codigo'), int(r_form('usu_codigo_comprador', 0)) or None, int(r_form('und_codigo', 0)) or None,
                float(r_form('prd_valor_venda', 0.0)) or None, r_form('prd_origem'), float(r_form('prd_percentual_icms', 0.0)) or None,
                float(r_form('prd_percentual_ipi', 0.0)) or None, r_form('etq_codigo_padrao'), 1 if 'prd_permite_comprar' in request.form else 0,
                int(r_form('udc_codigo', 0)) or None, float(r_form('prd_valor_unidade_conversao', 0.0)) or None
            )
            sql = "{CALL pr_produto_i (" + "?,"*29 + "?)}"

        cursor.execute(sql, params)
        conn.commit()
        
        action = "atualizado" if is_edit else "cadastrado"
        flash(f'Produto {action} com sucesso!', 'success')
        return redirect('/produtos')

    except (pyodbc.Error, ValueError, Exception) as e:
        if conn: conn.rollback()
        flash(f'Erro ao salvar produto: {e}', 'danger')
    finally:
        if conn: conn.close()
    return redirect(request.url)

# --- Rotas de Pedidos ---
@app.route('/pedidos')
def visualizar_pedidos():
    if 'usuario_logado' not in session: return redirect('/')
    conn = conectar_sqlserver()
    pedidos = []
    colunas = ['Código', 'Fornecedor', 'Entrega Inicial', 'Entrega Final', 'Qtd Total', 'Valor Total', 'Status']
    if conn:
        try:
            cur = conn.cursor()
            query = """
                SELECT 
                    p.ped_codigo AS 'Código',
                    pj.pju_razao_social AS 'Fornecedor',
                    p.ped_data_entrega_inicial AS 'Entrega Inicial',
                    p.ped_data_entrega_final AS 'Entrega Final',
                    p.ped_qtde_total AS 'Qtd Total',
                    p.ped_valor_total AS 'Valor Total',
                    p.ped_status AS 'Status'
                FROM tb_pedido p
                LEFT JOIN tb_pessoa_juridica pj ON p.pes_codigo = pj.pes_codigo
                ORDER BY p.ped_codigo DESC;
            """
            cur.execute(query)
            pedidos = cur.fetchall()
        except pyodbc.Error as e: flash(f'Erro ao buscar pedidos: {e}', 'danger')
        finally: conn.close()
    return render_template('visualizar_pedidos.html', dados=pedidos, colunas=colunas)

@app.route('/pedidos/excluir/<int:ped_codigo>', methods=['POST'])
def excluir_pedido(ped_codigo):
    if 'usuario_logado' not in session: return redirect('/')
    conn = None
    try:
        conn = conectar_sqlserver()
        if not conn: raise Exception("Não foi possível conectar ao banco de dados.")
        
        cursor = conn.cursor()
        sql = "{CALL pr_pedido_d (?)}"
        cursor.execute(sql, (ped_codigo))
        conn.commit()
        flash('Pedido excluído com sucesso!', 'success')
    except pyodbc.Error as e:
        conn.rollback()
        flash(f'Erro ao excluir pedido: {e}', 'danger')
    finally:
        if conn: conn.close()
    return redirect('/pedidos')

def get_pedido_form_data(conn):
    fornecedores, compradores, produtos, filiais = [], [], [], []
    if conn:
        try:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT pes_codigo, pju_razao_social 
                FROM tb_pessoa_juridica 
                WHERE pes_codigo IN (SELECT pes_codigo FROM tb_fornecedor)
                ORDER BY pju_razao_social
            """)
            fornecedores = cursor.fetchall()
            cursor.execute("SELECT usu_codigo, usu_login FROM tb_usuario WHERE usu_ativo = 1 ORDER BY usu_login")
            compradores = cursor.fetchall()
            cursor.execute("""
                SELECT 
                    p.prd_codigo, p.sec_codigo, p.esp_codigo, p.prd_descricao, p.prd_ultimo_custo,
                    m.mar_descricao, p.prd_referencia_fornec,
                    CONCAT(FORMAT(p.sec_codigo, '000'), FORMAT(p.esp_codigo, '00'), FORMAT(p.prd_codigo, '0000')) AS full_code
                FROM tb_produto p
                LEFT JOIN tb_marca m ON p.mar_codigo = m.mar_codigo
                WHERE p.prd_ativo = 1 
                ORDER BY p.prd_descricao
            """)
            produtos = cursor.fetchall()
            cursor.execute("SELECT fil_codigo, fil_descricao FROM tb_filial ORDER BY fil_descricao")
            filiais = cursor.fetchall()
        except pyodbc.Error as e: flash(f"Erro ao carregar dados: {e}", "danger")
    return fornecedores, compradores, produtos, filiais

@app.route('/api/product_details/<string:full_code>')
def api_product_details(full_code):
    if 'usuario_logado' not in session: return jsonify({"error": "Não autorizado"}), 401
    
    conn = conectar_sqlserver()
    if not conn: return jsonify({"error": "DB connection failed"}), 500

    produto = None
    try:
        cursor = conn.cursor()
        query = """
            SELECT prd_codigo, sec_codigo, esp_codigo, prd_descricao, prd_ultimo_custo
            FROM tb_produto 
            WHERE CONCAT(FORMAT(sec_codigo, '000'), FORMAT(esp_codigo, '00'), FORMAT(prd_codigo, '0000')) = ?
        """
        cursor.execute(query, full_code)
        rows = rows_to_dicts(cursor)
        produto = rows[0] if rows else None
    except pyodbc.Error as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

    if produto:
        return jsonify(produto)
    else:
        return jsonify({"error": "Produto não encontrado"}), 404


@app.route('/pedidos/cadastrar', methods=['GET', 'POST'])
def cadastrar_pedido():
    if 'usuario_logado' not in session: return redirect('/')
    if request.method == 'POST':
        return handle_pedido_post()
    
    conn = conectar_sqlserver()
    fornecedores, compradores, produtos, filiais = get_pedido_form_data(conn)
    if conn: conn.close()
    return render_template('pedido_form.html', pedido=None, itens_pedido='[]', fornecedores=fornecedores, compradores=compradores, produtos=produtos, filiais=filiais)

@app.route('/pedidos/editar/<int:ped_codigo>', methods=['GET', 'POST'])
def editar_pedido(ped_codigo):
    if 'usuario_logado' not in session: return redirect('/')
    if request.method == 'POST':
        return handle_pedido_post(is_edit=True)

    conn = None
    pedido, itens_pedido_dict = None, []
    try:
        conn = conectar_sqlserver()
        if not conn:
            flash("Não foi possível conectar ao banco de dados.", "danger")
            return redirect('/pedidos')

        cursor = conn.cursor()
        cursor.execute("SELECT * FROM tb_pedido WHERE ped_codigo=?", (ped_codigo,))
        pedido = cursor.fetchone()
        if not pedido:
            flash("Pedido não encontrado!", "danger")
            return redirect('/pedidos')
        
        cursor.execute("SELECT * FROM tb_item_pedido WHERE ped_codigo=?", (ped_codigo,))
        itens_pedido_dict = rows_to_dicts(cursor)
        
        fornecedores, compradores, produtos, filiais = get_pedido_form_data(conn)
        
    except pyodbc.Error as e: flash(f"Erro ao carregar dados do pedido: {e}", "danger")
    finally:
        if conn: conn.close()
        
    return render_template('pedido_form.html', pedido=pedido, itens_pedido=json.dumps(itens_pedido_dict, default=str), fornecedores=fornecedores, compradores=compradores, produtos=produtos, filiais=filiais)

def handle_pedido_post(is_edit=False):
    conn = None
    try:
        conn = conectar_sqlserver()
        if not conn: raise Exception("Não foi possível conectar ao banco de dados.")

        usu_codigo = session.get('usuario_codigo')
        cursor = conn.cursor()
        
        current_ped_codigo = int(r_form('ped_codigo', 0)) if is_edit else 0
        cursor.execute("DELETE FROM aux_item_pedido WHERE usu_codigo = ?", usu_codigo)
        cursor.execute("DELETE FROM aux_pedido_produto WHERE usu_codigo = ?", usu_codigo)

        # A estrutura de dados agora é uma lista de distribuições
        itens_distribuicao = json.loads(request.form.get('itens_pedido_json', '[]'))
        if not itens_distribuicao: raise ValueError("O pedido deve ter pelo menos um item.")

        # Agrupa os itens por produto para inserir em aux_pedido_produto
        produtos_agrupados = {}
        for item in itens_distribuicao:
            prd_codigo = item['prd_codigo']
            if prd_codigo not in produtos_agrupados:
                produtos_agrupados[prd_codigo] = {
                    'sec_codigo': item['sec_codigo'], 'esp_codigo': item['esp_codigo'],
                    'prd_codigo': prd_codigo, 'ppr_qtde_pedido': 0, 'ppr_custo_medio': item['custo']
                }
            produtos_agrupados[prd_codigo]['ppr_qtde_pedido'] += item['quantidade']

        for prd in produtos_agrupados.values():
            cursor.execute("INSERT INTO aux_pedido_produto (usu_codigo, ped_codigo, sec_codigo, esp_codigo, prd_codigo, ppr_qtde_pedido, ppr_custo_medio) VALUES (?, ?, ?, ?, ?, ?, ?)", 
                           usu_codigo, current_ped_codigo, prd['sec_codigo'], prd['esp_codigo'], prd['prd_codigo'], prd['ppr_qtde_pedido'], prd['ppr_custo_medio'])

        # Insere cada distribuição em aux_item_pedido
        for item in itens_distribuicao:
            cursor.execute("INSERT INTO aux_item_pedido (usu_codigo, ped_codigo, sec_codigo, esp_codigo, prd_codigo, ipr_codigo, fil_codigo, ipd_qtde_pedido, ipd_valor_custo) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", 
                           usu_codigo, current_ped_codigo, item['sec_codigo'], item['esp_codigo'], item['prd_codigo'], item['ipr_codigo'], item['fil_codigo'], item['quantidade'], item['custo'])

        if is_edit:
            params = (
                usu_codigo, current_ped_codigo, r_form('pes_codigo'), int(r_form('usu_codigo_comprador')),
                r_form('ped_data_emissao'), r_form('ped_data_entrega_inicial'), r_form('ped_data_entrega_final'),
                r_form('ped_status'), r_form('ped_observacao'), float(r_form('ped_qtde_total',0)),
                float(r_form('ped_valor_total',0)), float(r_form('ped_qtde_entregue_total',0)),
                float(r_form('ped_custo_medio',0)), r_form('ped_codigo_original'), r_form('ped_qualidade'),
                int(r_form('cpg_codigo',0)) or None,
            )
            sql = "{CALL pr_pedido_u (" + "?,"*15 + "?)}"
        else:
            params = (
                usu_codigo, 0, r_form('pes_codigo'), int(r_form('usu_codigo_comprador')),
                datetime.now(), r_form('ped_data_entrega_inicial'), r_form('ped_data_entrega_final'),
                'D', r_form('ped_observacao'), float(r_form('ped_qtde_total',0)),
                float(r_form('ped_valor_total',0)), 0,
                float(r_form('ped_custo_medio',0)), r_form('ped_codigo_original'), r_form('ped_qualidade'),
                int(r_form('cpg_codigo',0)) or None,
            )
            sql = "{CALL pr_pedido_i (" + "?,"*15 + "?)}"
        
        cursor.execute(sql, params)
        conn.commit()
        
        action = "atualizado" if is_edit else "cadastrado"
        flash(f'Pedido {action} com sucesso!', 'success')
        return redirect('/pedidos')

    except (pyodbc.Error, ValueError, KeyError, Exception) as e:
        if conn: conn.rollback()
        flash(f'Erro ao salvar pedido: {e}', 'danger')
    finally:
        if conn: conn.close()
    
    return redirect(request.url)

@app.route('/api/produto/grade/<int:sec_codigo>/<int:esp_codigo>/<int:prd_codigo>')
def api_produto_grade(sec_codigo, esp_codigo, prd_codigo):
    """
    Retorna os atributos de Cor e Tamanho disponíveis para um produto específico.
    """
    if 'usuario_logado' not in session:
        return jsonify({"error": "Não autorizado"}), 401
    
    conn = conectar_sqlserver()
    grade = {'cores': [], 'tamanhos': []}
    if conn:
        try:
            cursor = conn.cursor()
            # Query para buscar cores
            query_cores = """
                SELECT DISTINCT aip.aip_descricao
                FROM tb_atributo_item_produto aip
                JOIN tb_tipo_atributo tpa ON aip.tpa_codigo = tpa.tpa_codigo
                WHERE aip.sec_codigo = ? AND aip.esp_codigo = ? AND aip.prd_codigo = ?
                  AND UPPER(tpa.tpa_descricao) LIKE '%COR%'
                ORDER BY aip.aip_descricao;
            """
            cursor.execute(query_cores, (sec_codigo, esp_codigo, prd_codigo))
            grade['cores'] = [row.aip_descricao for row in cursor.fetchall()]

            # Query para buscar tamanhos
            query_tamanhos = """
                SELECT DISTINCT aip.aip_descricao
                FROM tb_atributo_item_produto aip
                JOIN tb_tipo_atributo tpa ON aip.tpa_codigo = tpa.tpa_codigo
                WHERE aip.sec_codigo = ? AND aip.esp_codigo = ? AND aip.prd_codigo = ?
                  AND UPPER(tpa.tpa_descricao) LIKE '%TAMANHO%'
                ORDER BY aip.aip_descricao;
            """
            cursor.execute(query_tamanhos, (sec_codigo, esp_codigo, prd_codigo))
            grade['tamanhos'] = [row.aip_descricao for row in cursor.fetchall()]
            
        except pyodbc.Error as e:
            return jsonify({"error": str(e)}), 500
        finally:
            conn.close()
    
    return jsonify(grade)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
