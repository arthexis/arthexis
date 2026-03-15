package body Apps.Test.Views.Test_Views is

   function Test_Toggle_SQL return String is
   begin
      return
        "SELECT id, test_name, enabled "
        & "FROM test_test "
        & "ORDER BY test_name";
   end Test_Toggle_SQL;

end Apps.Test.Views.Test_Views;
