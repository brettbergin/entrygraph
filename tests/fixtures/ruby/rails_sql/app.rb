# Rails-shaped fixture for the rb.sql-execute / rb.sql-query split (#91).
class ReportsController
  def create
    # service-object convention: NOT a SQL sink
    result = Reports::CreateService.new(params).execute(declared_params)
    update_service.execute(params[:options])
    result
  end

  def raw_lookup
    # interpolated raw SQL: IS a SQL sink
    ActiveRecord::Base.connection.execute("SELECT * FROM reports WHERE id = #{params[:id]}")
  end

  def raw_query
    # unambiguous ActiveRecord raw-SQL method: sink regardless of args
    Report.find_by_sql(sql_for(params))
  end
end
